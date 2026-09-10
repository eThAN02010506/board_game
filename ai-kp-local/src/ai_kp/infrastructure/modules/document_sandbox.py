"""Killable process boundary for parsing untrusted module documents."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

from ai_kp.infrastructure.document_process_sandbox import (
    DocumentProcessPolicy,
    DocumentProcessSandboxError,
    apply_posix_resource_limits,
    read_bounded_source,
    run_document_process_isolated,
    write_process_error,
)
from ai_kp.infrastructure.modules.legacy_doc_converter import (
    convert_legacy_doc_to_docx,
    terminate_recorded_converter_group,
)
from ai_kp.infrastructure.modules.mineru_parser import (
    MineruParseError,
    parse_with_mineru,
    terminate_recorded_mineru_group,
)
from ai_kp.platform.modules.documents import (
    MAX_ASSETS,
    MAX_DOCUMENT_CHUNKS,
    MAX_EXTRACTED_CHARACTERS,
    MAX_MODULE_BYTES,
    MAX_SINGLE_ASSET_BYTES,
    MAX_TOTAL_ASSET_BYTES,
    DocumentAsset,
    DocumentChunk,
    ExtractedModuleDocument,
)
from ai_kp.platform.modules.pdf_quality import assess_native_pdf

_RESULT_MANIFEST_MAX_BYTES = max(
    128 * 1024 * 1024,
    MAX_EXTRACTED_CHARACTERS * 8,
)
_RESULT_MANIFEST_NAME = "result.json"


class ModuleDocumentSandboxError(ValueError):
    """A bounded, user-safe failure raised by the parser process boundary."""


@dataclass(frozen=True)
class DocumentParsePolicy(DocumentProcessPolicy):
    """Resource budget applied to one PDF, DOC, or DOCX parsing attempt."""

    timeout_seconds: float = 10_800
    memory_limit_mib: int = 8192
    cpu_seconds: int = 10_800
    legacy_doc_converter_command: str = "soffice"
    legacy_doc_converter_timeout_seconds: float = 60
    parser: str = "native_first"
    mineru_command: str = "mineru"
    mineru_backend: str = "pipeline"
    mineru_model_source: str = "modelscope"

    def __post_init__(self) -> None:
        super().__post_init__()
        converter = self.legacy_doc_converter_command.strip()
        if (
            not converter
            or len(converter) > 500
            or any(character in converter for character in ("\x00", "\r", "\n"))
        ):
            raise ValueError("Legacy DOC converter command is invalid")
        if self.legacy_doc_converter_timeout_seconds <= 0:
            raise ValueError("Legacy DOC converter timeout must be positive")
        if self.parser not in {"native_first", "builtin", "mineru"}:
            raise ValueError("Module document parser must be native_first, builtin, or mineru")
        if self.mineru_backend not in {"pipeline", "vlm-engine", "hybrid-engine"}:
            raise ValueError("Unsupported local MinerU backend")


def extract_module_document_isolated(
    source_path: Path,
    source_filename: str,
    *,
    title: str,
    expected_hash: str,
    policy: DocumentParsePolicy,
) -> ExtractedModuleDocument:
    """Parse one private source file in a spawn-based, killable subprocess."""

    try:
        return run_document_process_isolated(
            source_path,
            operation_label="KP 本解析",
            policy=policy,
            target=_parse_document_child,
            child_args=(source_filename, title, expected_hash),
            read_result=lambda result_root: _read_child_result(
                result_root / _RESULT_MANIFEST_NAME,
                expected_hash=expected_hash,
            ),
            termination_hook=lambda result_root, force: (
                terminate_recorded_converter_group(result_root, force=force),
                terminate_recorded_mineru_group(result_root, force=force),
            ),
        )
    except DocumentProcessSandboxError as exc:
        raise ModuleDocumentSandboxError(str(exc)) from exc


def _parse_document_child(
    result_root: str,
    source_path: str,
    source_filename: str,
    title: str,
    expected_hash: str,
    policy: DocumentParsePolicy,
) -> None:
    try:
        apply_posix_resource_limits(policy)
        data = read_bounded_source(
            Path(source_path),
            max_bytes=MAX_MODULE_BYTES,
            label="KP 本",
        )
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError("KP 本源文件哈希校验失败")

        if Path(source_filename.lower()).suffix == ".doc":
            converted = convert_legacy_doc_to_docx(
                data,
                Path(result_root),
                converter_command=policy.legacy_doc_converter_command,
                timeout_seconds=min(
                    policy.legacy_doc_converter_timeout_seconds,
                    max(0.1, policy.timeout_seconds * 0.8),
                ),
            )
            parse_data = converted
            parse_filename = f"{Path(source_filename).stem}.docx"
        else:
            parse_data = data
            parse_filename = source_filename
        extracted = _parse_with_policy(
            parse_data,
            parse_filename,
            result_root=Path(result_root),
            title=title,
            source_hash=expected_hash,
            policy=policy,
        )
        if Path(source_filename.lower()).suffix == ".doc":
            extracted = replace(extracted, source_hash=expected_hash)
        if extracted.source_hash != expected_hash:
            raise ValueError("解析结果与源文件哈希不一致")
        _write_child_result(Path(result_root), extracted)
    except MemoryError:
        write_process_error(Path(result_root), "KP 本解析超过内存限制")
    except BaseException as exc:  # noqa: BLE001 - child must report bounded failure
        message = str(exc).strip() or type(exc).__name__
        write_process_error(Path(result_root), message)


def _parse_with_policy(
    data: bytes,
    filename: str,
    *,
    result_root: Path,
    title: str,
    source_hash: str,
    policy: DocumentParsePolicy,
) -> ExtractedModuleDocument:
    if policy.parser == "mineru":
        return _parse_mineru(
            data,
            filename,
            result_root=result_root,
            title=title,
            source_hash=source_hash,
            policy=policy,
        )

    from ai_kp.platform.modules.documents import extract_module_document

    native = extract_module_document(data, filename, title=title)
    if policy.parser == "builtin" or native.source_type != "pdf":
        return native

    quality = assess_native_pdf(native)
    if quality.accepted:
        return native
    try:
        fallback = _parse_mineru(
            data,
            filename,
            result_root=result_root,
            title=title,
            source_hash=source_hash,
            policy=policy,
        )
    except MineruParseError as exc:
        reasons = ", ".join(quality.reasons)
        raise ValueError(f"内置 PDF 文本质量不足（{reasons}）；MinerU 降级失败：{exc}") from exc
    return _add_review_flags(
        fallback,
        ("native_pdf_fallback", *quality.reasons),
    )


def _parse_mineru(
    data: bytes,
    filename: str,
    *,
    result_root: Path,
    title: str,
    source_hash: str,
    policy: DocumentParsePolicy,
) -> ExtractedModuleDocument:
    return parse_with_mineru(
        data,
        filename,
        result_root,
        title=title,
        source_hash=source_hash,
        command=policy.mineru_command,
        backend=policy.mineru_backend,
        model_source=policy.mineru_model_source,
        timeout_seconds=max(1, policy.timeout_seconds * 0.95),
    )


def _add_review_flags(
    document: ExtractedModuleDocument,
    flags: tuple[str, ...],
) -> ExtractedModuleDocument:
    return replace(
        document,
        chunks=tuple(
            replace(chunk, review_flags=(*flags, *chunk.review_flags)) for chunk in document.chunks
        ),
        assets=tuple(
            replace(asset, review_flags=(*flags, *asset.review_flags)) for asset in document.assets
        ),
    )


def _write_child_result(
    result_root: Path,
    extracted: ExtractedModuleDocument,
) -> None:
    assets = []
    for index, asset in enumerate(extracted.assets):
        data_filename = f"asset-{index:04d}.bin"
        (result_root / data_filename).write_bytes(asset.data)
        assets.append(
            {
                "data_file": data_filename,
                "filename": asset.filename,
                "mime_type": asset.mime_type,
                "source_locator": asset.source_locator,
                "nearby_heading": asset.nearby_heading,
                "width": asset.width,
                "height": asset.height,
                "asset_role": asset.asset_role,
                "classification_confidence": asset.classification_confidence,
                "review_flags": list(asset.review_flags),
            }
        )
    manifest = {
        "source_type": extracted.source_type,
        "source_hash": extracted.source_hash,
        "title": extracted.title,
        "unit_count": extracted.unit_count,
        "chunks": [
            {
                "title": chunk.title,
                "text": chunk.text,
                "order_index": chunk.order_index,
                "content_kind": chunk.content_kind,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "paragraph_start": chunk.paragraph_start,
                "paragraph_end": chunk.paragraph_end,
                "source_locator": chunk.source_locator,
                "semantic_kind": chunk.semantic_kind,
                "classification_confidence": chunk.classification_confidence,
                "style_annotations": list(chunk.style_annotations),
                "review_flags": list(chunk.review_flags),
                "heading_level": chunk.heading_level,
                "section_path": list(chunk.section_path),
                "scene_key": chunk.scene_key,
            }
            for chunk in extracted.chunks
        ],
        "assets": assets,
    }
    temporary_path = result_root / f"{_RESULT_MANIFEST_NAME}.tmp"
    with temporary_path.open("w", encoding="utf-8") as output:
        json.dump(manifest, output, ensure_ascii=False, separators=(",", ":"))
    temporary_path.replace(result_root / _RESULT_MANIFEST_NAME)


def _read_child_result(
    manifest_path: Path,
    *,
    expected_hash: str,
) -> ExtractedModuleDocument:
    try:
        manifest_size = manifest_path.stat().st_size
    except OSError as exc:
        raise ModuleDocumentSandboxError(
            "KP 本解析子进程未返回结果"
        ) from exc
    if manifest_size <= 0 or manifest_size > _RESULT_MANIFEST_MAX_BYTES:
        raise ModuleDocumentSandboxError("KP 本解析结果清单超过安全限制")
    try:
        with manifest_path.open("r", encoding="utf-8") as source:
            manifest = json.load(source)
        if not isinstance(manifest, dict):
            raise TypeError
        if manifest.get("source_hash") != expected_hash:
            raise ModuleDocumentSandboxError("解析结果与源文件哈希不一致")
        source_type = manifest["source_type"]
        if source_type not in {"pdf", "docx"}:
            raise TypeError
        raw_chunks = manifest["chunks"]
        raw_assets = manifest["assets"]
        if not isinstance(raw_chunks, list) or not isinstance(raw_assets, list):
            raise TypeError
        if len(raw_chunks) > MAX_DOCUMENT_CHUNKS:
            raise ModuleDocumentSandboxError("KP 本解析结果文本块数量超过安全限制")
        if len(raw_assets) > MAX_ASSETS:
            raise ModuleDocumentSandboxError("KP 本解析结果图片数量超过安全限制")
        chunks = tuple(_decode_chunk(item) for item in raw_chunks)
        assets = _decode_assets(manifest_path.parent, raw_assets)
        return ExtractedModuleDocument(
            source_type=source_type,
            source_hash=expected_hash,
            title=str(manifest["title"]),
            unit_count=int(manifest["unit_count"]),
            chunks=chunks,
            assets=assets,
        )
    except ModuleDocumentSandboxError:
        raise
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ModuleDocumentSandboxError(
            "KP 本解析子进程返回了无效结果"
        ) from exc


def _decode_chunk(item: object) -> DocumentChunk:
    if not isinstance(item, dict):
        raise TypeError
    return DocumentChunk(
        title=str(item["title"]),
        text=str(item["text"]),
        order_index=int(item["order_index"]),
        content_kind=str(item["content_kind"]),
        page_start=_optional_int(item.get("page_start")),
        page_end=_optional_int(item.get("page_end")),
        paragraph_start=_optional_int(item.get("paragraph_start")),
        paragraph_end=_optional_int(item.get("paragraph_end")),
        source_locator=str(item["source_locator"]),
        semantic_kind=str(item["semantic_kind"]),
        classification_confidence=float(item["classification_confidence"]),
        style_annotations=_string_tuple(item["style_annotations"]),
        review_flags=_string_tuple(item["review_flags"]),
        heading_level=_optional_int(item.get("heading_level")),
        section_path=_string_tuple(item.get("section_path", [])),
        scene_key=(
            str(item["scene_key"])
            if isinstance(item.get("scene_key"), str) and item["scene_key"].strip()
            else None
        ),
    )


def _decode_assets(
    result_root: Path,
    raw_assets: list[object],
) -> tuple[DocumentAsset, ...]:
    assets: list[DocumentAsset] = []
    asset_bytes = 0
    for index, item in enumerate(raw_assets):
        if not isinstance(item, dict):
            raise TypeError
        expected_filename = f"asset-{index:04d}.bin"
        if item.get("data_file") != expected_filename:
            raise TypeError
        data_path = result_root / expected_filename
        size = data_path.stat().st_size
        if size > MAX_SINGLE_ASSET_BYTES:
            raise ModuleDocumentSandboxError(
                "KP 本解析结果存在超过安全限制的单张图片"
            )
        asset_bytes += size
        if asset_bytes > MAX_TOTAL_ASSET_BYTES:
            raise ModuleDocumentSandboxError("KP 本解析结果图片总大小超过安全限制")
        with data_path.open("rb") as source:
            data = source.read(MAX_SINGLE_ASSET_BYTES + 1)
        if len(data) != size:
            raise ModuleDocumentSandboxError("KP 本解析结果图片不完整")
        assets.append(
            DocumentAsset(
                data=data,
                filename=str(item["filename"]),
                mime_type=str(item["mime_type"]),
                source_locator=str(item["source_locator"]),
                nearby_heading=(
                    None
                    if item.get("nearby_heading") is None
                    else str(item["nearby_heading"])
                ),
                width=_optional_int(item.get("width")),
                height=_optional_int(item.get("height")),
                asset_role=str(item["asset_role"]),
                classification_confidence=float(
                    item["classification_confidence"]
                ),
                review_flags=_string_tuple(item["review_flags"]),
            )
        )
    return tuple(assets)


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError
    return tuple(str(item) for item in value)
