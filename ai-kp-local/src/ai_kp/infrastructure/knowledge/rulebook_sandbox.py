"""Spawn-isolated rulebook PDF extraction with a bounded disk manifest."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ai_kp.infrastructure.document_process_sandbox import (
    DocumentProcessPolicy,
    DocumentProcessSandboxError,
    apply_posix_resource_limits,
    read_bounded_source,
    run_document_process_isolated,
    write_process_error,
)
from ai_kp.infrastructure.knowledge.pdf_ingestion import (
    MAX_EXTRACTED_CHARACTERS,
    MAX_PDF_BYTES,
    MAX_PDF_PAGES,
    extract_rulebook_pdf,
)
from ai_kp.platform.knowledge.sources import ExtractedRulebook

_RESULT_MANIFEST_NAME = "rulebook-result.json"
_RESULT_MANIFEST_MAX_BYTES = max(
    128 * 1024 * 1024,
    MAX_EXTRACTED_CHARACTERS * 8,
)
_MAX_METADATA_ENTRIES = 200
_MAX_METADATA_VALUE_CHARACTERS = 20_000
_MAX_RULEBOOK_CHUNKS = 100_000


class RulebookDocumentSandboxError(ValueError):
    """A bounded rulebook extraction failure safe to return to a local admin."""


def extract_rulebook_pdf_isolated(
    data: bytes,
    filename: str,
    *,
    policy: DocumentProcessPolicy,
) -> ExtractedRulebook:
    """Extract one rulebook in a spawn child; large results travel through disk."""

    if not data or len(data) > MAX_PDF_BYTES:
        raise RulebookDocumentSandboxError("规则书 PDF 为空或超过 64 MiB 限制")
    if not data.startswith(b"%PDF-"):
        raise RulebookDocumentSandboxError("规则书扩展名为 PDF，但文件签名不正确")
    expected_hash = hashlib.sha256(data).hexdigest()
    source_root = Path(tempfile.mkdtemp(prefix=".rulebook-source-"))
    source_path = source_root / "source.pdf"
    try:
        source_path.write_bytes(data)
        source_path.chmod(0o600)
        try:
            return run_document_process_isolated(
                source_path,
                operation_label="规则书 PDF 解析",
                policy=policy,
                target=_extract_rulebook_child,
                child_args=(filename, expected_hash),
                read_result=lambda result_root: _read_result_manifest(
                    result_root / _RESULT_MANIFEST_NAME,
                    expected_hash=expected_hash,
                ),
            )
        except DocumentProcessSandboxError as exc:
            raise RulebookDocumentSandboxError(str(exc)) from exc
    finally:
        shutil.rmtree(source_root, ignore_errors=True)


def _extract_rulebook_child(
    result_root: str,
    source_path: str,
    filename: str,
    expected_hash: str,
    policy: DocumentProcessPolicy,
) -> None:
    try:
        apply_posix_resource_limits(policy)
        data = read_bounded_source(
            Path(source_path),
            max_bytes=MAX_PDF_BYTES,
            label="规则书 PDF",
        )
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError("规则书 PDF 源文件哈希校验失败")
        extracted = extract_rulebook_pdf(data, filename)
        if extracted.source_hash != expected_hash:
            raise ValueError("规则书 PDF 解析结果与源文件哈希不一致")
        _write_result_manifest(Path(result_root), extracted)
    except MemoryError:
        write_process_error(Path(result_root), "规则书 PDF 解析超过内存限制")
    except BaseException as exc:  # noqa: BLE001 - child reports a bounded error
        write_process_error(
            Path(result_root),
            str(exc).strip() or type(exc).__name__,
        )


def _write_result_manifest(
    result_root: Path,
    extracted: ExtractedRulebook,
) -> None:
    manifest = {
        "source_hash": extracted.source_hash,
        "page_count": extracted.page_count,
        "title": extracted.title,
        "metadata": extracted.metadata,
        "chunks": extracted.chunks,
    }
    temporary = result_root / f"{_RESULT_MANIFEST_NAME}.tmp"
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(manifest, output, ensure_ascii=False, separators=(",", ":"))
    temporary.replace(result_root / _RESULT_MANIFEST_NAME)


def _read_result_manifest(
    manifest_path: Path,
    *,
    expected_hash: str,
) -> ExtractedRulebook:
    try:
        size = manifest_path.stat().st_size
    except OSError as exc:
        raise RulebookDocumentSandboxError(
            "规则书 PDF 解析子进程未返回结果"
        ) from exc
    if size <= 0 or size > _RESULT_MANIFEST_MAX_BYTES:
        raise RulebookDocumentSandboxError("规则书 PDF 解析结果清单超过安全限制")
    try:
        with manifest_path.open("r", encoding="utf-8") as source:
            manifest = json.load(source)
        if not isinstance(manifest, dict):
            raise TypeError
        if manifest.get("source_hash") != expected_hash:
            raise RulebookDocumentSandboxError("规则书 PDF 解析结果哈希不一致")
        page_count = int(manifest["page_count"])
        if page_count <= 0 or page_count > MAX_PDF_PAGES:
            raise TypeError
        title = manifest["title"]
        metadata = manifest["metadata"]
        chunks = manifest["chunks"]
        if not isinstance(title, str) or not title or len(title) > 2_000:
            raise TypeError
        normalized_metadata = _decode_metadata(metadata)
        normalized_chunks = _decode_chunks(chunks, expected_hash)
        return ExtractedRulebook(
            source_hash=expected_hash,
            page_count=page_count,
            title=title,
            metadata=normalized_metadata,
            chunks=normalized_chunks,
        )
    except RulebookDocumentSandboxError:
        raise
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise RulebookDocumentSandboxError(
            "规则书 PDF 解析子进程返回了无效结果"
        ) from exc


def _decode_metadata(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or len(value) > _MAX_METADATA_ENTRIES:
        raise TypeError
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or len(raw_key) > 200:
            raise TypeError
        if not isinstance(raw_value, str):
            raise TypeError
        if len(raw_value) > _MAX_METADATA_VALUE_CHARACTERS:
            raise TypeError
        result[raw_key] = raw_value
    return result


def _decode_chunks(value: object, source_hash: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > _MAX_RULEBOOK_CHUNKS:
        raise TypeError
    chunks: list[dict[str, Any]] = []
    extracted_characters = 0
    for expected_order, item in enumerate(value):
        if not isinstance(item, dict):
            raise TypeError
        page_start = int(item["page_start"])
        page_end = int(item["page_end"])
        order_index = int(item["order_index"])
        text = item["text"]
        if (
            not isinstance(text, str)
            or not text
            or order_index != expected_order
            or page_start <= 0
            or page_end < page_start
            or page_end > MAX_PDF_PAGES
        ):
            raise TypeError
        extracted_characters += len(text)
        if extracted_characters > MAX_EXTRACTED_CHARACTERS:
            raise TypeError
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if item.get("text_hash") != text_hash:
            raise TypeError
        expected_id = (
            f"rulechunk_{source_hash[:12]}_{page_start:04d}_{order_index:05d}"
        )
        if item.get("id") != expected_id:
            raise TypeError
        chapter = item.get("chapter")
        section = item.get("section")
        if chapter is not None and (not isinstance(chapter, str) or len(chapter) > 160):
            raise TypeError
        if section is not None and (not isinstance(section, str) or len(section) > 160):
            raise TypeError
        if item.get("content_kind") != "text" or item.get("audience") != "kp":
            raise TypeError
        chunks.append(
            {
                "id": expected_id,
                "page_start": page_start,
                "page_end": page_end,
                "order_index": order_index,
                "chapter": chapter,
                "section": section,
                "content_kind": "text",
                "audience": "kp",
                "text": text,
                "text_hash": text_hash,
            }
        )
    return chunks
