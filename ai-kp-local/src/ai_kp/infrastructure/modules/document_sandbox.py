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

    timeout_seconds: float = 90
    memory_limit_mib: int = 1024
    cpu_seconds: int = 60
    legacy_doc_converter_command: str = "soffice"
    legacy_doc_converter_timeout_seconds: float = 60

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
                terminate_recorded_converter_group(result_root, force=force)
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

        # Resolve the parser after applying limits; every untrusted decoding
        # path executes inside the child resource budget.
        from ai_kp.platform.modules.documents import extract_module_document

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
            extracted = extract_module_document(
                converted,
                f"{Path(source_filename).stem}.docx",
                title=title,
            )
            extracted = replace(extracted, source_hash=expected_hash)
        else:
            extracted = extract_module_document(data, source_filename, title=title)
        if extracted.source_hash != expected_hash:
            raise ValueError("解析结果与源文件哈希不一致")
        _write_child_result(Path(result_root), extracted)
    except MemoryError:
        write_process_error(Path(result_root), "KP 本解析超过内存限制")
    except BaseException as exc:  # noqa: BLE001 - child must report bounded failure
        message = str(exc).strip() or type(exc).__name__
        write_process_error(Path(result_root), message)


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
