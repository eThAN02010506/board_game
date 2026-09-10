"""Normalize MinerU structured output into the platform document contract."""

from __future__ import annotations

import html
import json
import mimetypes
import re
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError

from ai_kp.platform.modules.documents import (
    MAX_ASSETS,
    MAX_CHUNK_CHARACTERS,
    MAX_DOCUMENT_CHUNKS,
    MAX_EXTRACTED_CHARACTERS,
    DocumentAsset,
    DocumentChunk,
    ExtractedModuleDocument,
    _image_dimensions,
    _track_asset_limits,
)
from ai_kp.platform.modules.section_ancestry import (
    inferred_scene_key,
    resolve_heading_level,
)
from ai_kp.platform.modules.structure import (
    infer_asset_role,
    infer_semantic_kind,
    inherit_heading_semantics,
    looks_like_heading,
)

MineruSourceType = Literal["pdf", "docx"]
_TAG = re.compile(r"<[^>]+>")


def extract_mineru_document(
    output_root: Path,
    *,
    title: str,
    source_hash: str,
    source_type: MineruSourceType,
    page_offset: int = 0,
) -> ExtractedModuleDocument:
    """Read stable MinerU content-list output without importing MinerU itself."""

    content_path = _single_output(output_root, "*_content_list.json")
    payload = json.loads(content_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise TypeError("MinerU content list must be an array")
    middle_blocks = _middle_blocks(output_root)
    aligned_middle = middle_blocks if len(middle_blocks) == len(payload) else []

    chunks: list[DocumentChunk] = []
    assets: list[DocumentAsset] = []
    current_heading = title
    section_headings: dict[int, str] = {}
    structural_parent_level: int | None = None
    total_characters = 0
    asset_bytes = 0
    image_pixels = 0
    for source_index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        page = _page_number(item, source_type, page_offset=page_offset)
        middle = aligned_middle[source_index] if aligned_middle else {}
        annotations = _style_annotations(middle)
        text = _content_text(item)
        item_type = str(item.get("type") or "text")
        explicit_heading_level = _heading_level(item)
        is_heading = _is_heading(item, middle, text, annotations)
        if is_heading:
            current_heading = _plain_text(text)[:240] or current_heading
        heading_level, structural_parent_level = resolve_heading_level(
            title=current_heading if is_heading else "",
            is_heading=is_heading,
            explicit_level=explicit_heading_level,
            structural_parent_level=structural_parent_level,
            prefer_structural_parent_for_generic_explicit=True,
        )
        if heading_level is not None:
            section_headings = {
                level: heading
                for level, heading in section_headings.items()
                if level < heading_level
            }
            section_headings[heading_level] = current_heading
        section_path = tuple(
            section_headings[level] for level in sorted(section_headings)
        )

        for part_index, part in enumerate(_split_text(text)):
            normalized = _plain_text(part)
            if not normalized:
                continue
            hint = infer_semantic_kind(
                normalized,
                content_kind="heading" if is_heading else item_type,
                styled_heading=is_heading,
            )
            hint = inherit_heading_semantics(hint, current_heading)
            total_characters += len(normalized)
            if total_characters > MAX_EXTRACTED_CHARACTERS:
                raise ValueError("MinerU 提取文本超过安全字符上限")
            chunks.append(
                DocumentChunk(
                    title=current_heading,
                    text=normalized,
                    order_index=len(chunks),
                    content_kind="heading" if is_heading else item_type,
                    page_start=page,
                    page_end=page,
                    source_locator=(
                        f"mineru:page:{page or 1}:block:{source_index + 1}"
                        f":part:{part_index + 1}"
                    ),
                    semantic_kind=hint.semantic_kind,
                    classification_confidence=hint.confidence,
                    style_annotations=annotations,
                    review_flags=(
                        "mineru_structured_extraction",
                        *hint.review_flags,
                    ),
                    heading_level=heading_level if is_heading else None,
                    section_path=section_path,
                    scene_key=inferred_scene_key(section_path),
                )
            )
            if len(chunks) > MAX_DOCUMENT_CHUNKS:
                raise ValueError("MinerU 提取文本块超过安全上限")

        image_path = item.get("img_path")
        if not isinstance(image_path, str) or not image_path.strip():
            continue
        resolved_image = _safe_output_path(content_path.parent, image_path)
        data = resolved_image.read_bytes()
        width, height = _image_dimensions(data)
        asset_role, asset_confidence, asset_flags = infer_asset_role(
            filename=resolved_image.name,
            nearby_heading=current_heading,
            width=width,
            height=height,
        )
        asset_bytes, image_pixels = _track_asset_limits(
            data,
            width,
            height,
            count=len(assets) + 1,
            asset_bytes=asset_bytes,
            image_pixels=image_pixels,
        )
        mime_type = _safe_raster_mime(data, resolved_image)
        assets.append(
            DocumentAsset(
                data=data,
                filename=resolved_image.name,
                mime_type=mime_type,
                source_locator=f"mineru:page:{page or 1}:asset:{len(assets) + 1}",
                nearby_heading=current_heading,
                width=width,
                height=height,
                asset_role=asset_role,
                classification_confidence=asset_confidence,
                review_flags=("mineru_structured_extraction", *asset_flags),
            )
        )
        if len(assets) > MAX_ASSETS:
            raise ValueError("MinerU 提取图片超过安全上限")

    if not chunks:
        raise ValueError("MinerU 没有提取到可用文本")
    pages = [chunk.page_end for chunk in chunks if chunk.page_end is not None]
    return ExtractedModuleDocument(
        source_type=source_type,
        source_hash=source_hash,
        title=title,
        unit_count=max(pages, default=1),
        chunks=tuple(chunks),
        assets=tuple(assets),
    )


def _single_output(output_root: Path, pattern: str) -> Path:
    matches = sorted(output_root.rglob(pattern))
    if len(matches) != 1:
        raise ValueError(f"MinerU 输出 {pattern} 数量异常：{len(matches)}")
    return matches[0]


def _middle_blocks(output_root: Path) -> list[dict[str, Any]]:
    matches = sorted(output_root.rglob("*_middle.json"))
    if len(matches) != 1:
        return []
    payload = json.loads(matches[0].read_text(encoding="utf-8"))
    pages = payload.get("pdf_info", []) if isinstance(payload, dict) else []
    return [
        block
        for page in pages
        if isinstance(page, dict)
        for block in page.get("para_blocks", [])
        if isinstance(block, dict)
    ]


def _content_text(item: dict[str, Any]) -> str:
    item_type = str(item.get("type") or "")
    if item_type == "list":
        values = item.get("list_items") or []
        return "\n".join(str(value) for value in values)
    if item_type == "table":
        values = [*(item.get("table_caption") or []), item.get("table_body") or ""]
        return "\n".join(str(value) for value in values if value)
    if item_type in {"image", "chart"}:
        values = [
            *(item.get("image_caption") or item.get("chart_caption") or []),
            *(item.get("image_footnote") or item.get("chart_footnote") or []),
        ]
        return "\n".join(str(value) for value in values if value)
    return str(item.get("text") or "")


def _plain_text(value: str) -> str:
    normalized = " ".join(html.unescape(_TAG.sub(" ", value)).split()).strip()
    normalized = re.sub(r"^#{1,6}\s+", "", normalized)
    for marker in ("***", "___", "**", "__", "*", "_"):
        if normalized.startswith(marker) and normalized.endswith(marker):
            normalized = normalized[len(marker) : -len(marker)].strip()
            break
    return normalized


def _split_text(value: str) -> tuple[str, ...]:
    normalized = value.strip()
    if not normalized:
        return ()
    parts: list[str] = []
    for line in normalized.splitlines() or [normalized]:
        line = line.strip()
        while len(line) > MAX_CHUNK_CHARACTERS:
            parts.append(line[:MAX_CHUNK_CHARACTERS])
            line = line[MAX_CHUNK_CHARACTERS:]
        if line:
            parts.append(line)
    return tuple(parts)


def _page_number(
    item: dict[str, Any],
    source_type: MineruSourceType,
    *,
    page_offset: int,
) -> int | None:
    if source_type != "pdf":
        return None
    raw = item.get("page_idx")
    return int(raw) + 1 + page_offset if isinstance(raw, int) and raw >= 0 else None


def _style_annotations(block: dict[str, Any]) -> tuple[str, ...]:
    styles = {
        str(style)
        for span in _walk_dicts(block)
        for style in (span.get("style") or [])
        if isinstance(style, str)
    }
    return tuple(sorted(styles))


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_dicts(nested)


def _is_heading(
    item: dict[str, Any],
    middle: dict[str, Any],
    text: str,
    annotations: tuple[str, ...],
) -> bool:
    level = item.get("text_level")
    if isinstance(level, int) and level > 0:
        return True
    if str(middle.get("type") or "").casefold() in {"title", "doc_title"}:
        return True
    normalized = _plain_text(text)
    return bool(
        "bold" in annotations
        and _all_text_spans_are_bold(middle)
        and looks_like_heading(normalized, styled_heading=True)
        and len(normalized) <= 80
    )


def _heading_level(item: dict[str, Any]) -> int | None:
    level = item.get("text_level")
    return (
        level
        if isinstance(level, int) and not isinstance(level, bool) and 1 <= level <= 9
        else None
    )


def _all_text_spans_are_bold(block: dict[str, Any]) -> bool:
    text_spans = tuple(
        span
        for span in _walk_dicts(block)
        if span.get("type") == "text" and str(span.get("content") or "").strip()
    )
    return bool(text_spans) and all(
        "bold" in (span.get("style") or []) for span in text_spans
    )


def _safe_output_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise ValueError("MinerU 图片路径越界或不存在")
    return candidate


def _safe_raster_mime(data: bytes, path: Path) -> str:
    try:
        with Image.open(BytesIO(data)) as image:
            image_format = str(image.format or "").upper()
            image.verify()
    except (OSError, SyntaxError, UnidentifiedImageError) as exc:
        raise ValueError("MinerU 图片产物不是有效的栅格图片") from exc
    mime_type = Image.MIME.get(image_format)
    extension_mime = mimetypes.guess_type(path.name)[0]
    if mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        raise ValueError("MinerU 图片产物使用不支持的格式")
    if extension_mime is not None and extension_mime != mime_type:
        raise ValueError("MinerU 图片扩展名与内容格式不一致")
    return mime_type


__all__ = ["extract_mineru_document"]
