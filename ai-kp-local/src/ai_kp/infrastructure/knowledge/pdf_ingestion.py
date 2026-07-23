"""Quarantined PDF text extraction adapter with page provenance."""

from __future__ import annotations

import hashlib
import re
from io import BytesIO
from typing import Any

from pypdf import PdfReader

from ai_kp.platform.knowledge.sources import ExtractedRulebook

MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_CHUNK_CHARACTERS = 2600
HEADING_PATTERN = re.compile(r"^(第[一二三四五六七八九十百]+章|\d+(?:\.\d+)+)\s*(.+)?$")


def _normalize_page_text(text: str) -> str:
    lines = []
    for raw in text.replace("\u00a0", " ").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or line == "克苏鲁的呼唤 第七版" or line.isdigit():
            continue
        lines.append(line)
    return "\n".join(lines)


def _split_page(text: str) -> list[str]:
    if not text:
        return []
    blocks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        if current and size + len(line) + 1 > MAX_CHUNK_CHARACTERS:
            blocks.append("\n".join(current))
            current = []
            size = 0
        current.append(line)
        size += len(line) + 1
    if current:
        blocks.append("\n".join(current))
    return blocks


def _page_blocks(page: Any, page_number: int) -> list[str]:
    try:
        return _split_page(_normalize_page_text(page.extract_text() or ""))
    except Exception as error:
        raise ValueError(f"第 {page_number} 页文本提取失败") from error


def _heading_scope(
    block: str,
    chapter: str | None,
    section: str | None,
) -> tuple[str | None, str | None]:
    for line in block.splitlines():
        heading = HEADING_PATTERN.match(line)
        if not heading:
            continue
        label = line[:160]
        return (label, section) if label.startswith("第") and "章" in label else (
            chapter,
            label,
        )
    return chapter, section


def _extract_chunks(reader: PdfReader, source_hash: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    chapter: str | None = None
    section: str | None = None
    for page_number, page in enumerate(reader.pages, start=1):
        for block in _page_blocks(page, page_number):
            chapter, section = _heading_scope(block, chapter, section)
            order_index = len(chunks)
            chunks.append(
                {
                    "id": (
                        f"rulechunk_{source_hash[:12]}_"
                        f"{page_number:04d}_{order_index:05d}"
                    ),
                    "page_start": page_number,
                    "page_end": page_number,
                    "order_index": order_index,
                    "chapter": chapter,
                    "section": section,
                    "content_kind": "text",
                    "audience": "all",
                    "text": block,
                    "text_hash": hashlib.sha256(block.encode("utf-8")).hexdigest(),
                }
            )
    return chunks


def extract_rulebook_pdf(data: bytes, filename: str) -> ExtractedRulebook:
    if not data or len(data) > MAX_PDF_BYTES:
        raise ValueError("规则书 PDF 为空或超过 64 MiB 限制")
    source_hash = hashlib.sha256(data).hexdigest()
    try:
        reader = PdfReader(BytesIO(data), strict=True)
    except Exception as exc:
        raise ValueError("无法解析规则书 PDF") from exc
    if reader.is_encrypted:
        raise ValueError("不接受加密的规则书 PDF")

    metadata = {
        str(key).lstrip("/"): str(value)
        for key, value in (reader.metadata or {}).items()
        if value is not None
    }
    title = metadata.get("Title") or filename.rsplit(".", 1)[0]
    chunks = _extract_chunks(reader, source_hash)
    if not chunks:
        raise ValueError("规则书没有可提取的文本层；需要先执行 OCR")
    return ExtractedRulebook(
        source_hash=source_hash,
        page_count=len(reader.pages),
        title=title,
        metadata=metadata,
        chunks=chunks,
    )
