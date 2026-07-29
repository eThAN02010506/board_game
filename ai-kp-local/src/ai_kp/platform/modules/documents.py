"""Bounded PDF and DOCX extraction with stable source provenance."""

from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Literal
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader

from ai_kp.platform.modules.structure import (
    infer_asset_role,
    infer_semantic_kind,
    looks_like_heading,
)

DocumentType = Literal["pdf", "docx"]
PARSER_VERSION = "module-document.v2"
MAX_MODULE_BYTES = 64 * 1024 * 1024
MAX_PDF_PAGES = 1200
MAX_ARCHIVE_MEMBERS = 10_000
MAX_DOCX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_DOCX_XML_BYTES = 32 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 12_000_000
MAX_CHUNK_CHARACTERS = 2600
MAX_ASSETS = 500
MAX_SINGLE_ASSET_BYTES = 32 * 1024 * 1024
MAX_TOTAL_ASSET_BYTES = 256 * 1024 * 1024
MAX_SINGLE_IMAGE_PIXELS = 50_000_000
MAX_TOTAL_IMAGE_PIXELS = 250_000_000
SAFE_RASTER_MIME_TYPES = {
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_UNSAFE_XML = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


@dataclass(frozen=True)
class DocumentChunk:
    title: str
    text: str
    order_index: int
    content_kind: str = "text"
    page_start: int | None = None
    page_end: int | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    source_locator: str = ""
    semantic_kind: str = "text"
    classification_confidence: float = 0
    style_annotations: tuple[str, ...] = ()
    review_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentAsset:
    data: bytes
    filename: str
    mime_type: str
    source_locator: str
    nearby_heading: str | None = None
    width: int | None = None
    height: int | None = None
    asset_role: str = "unknown"
    classification_confidence: float = 0
    review_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractedModuleDocument:
    source_type: DocumentType
    source_hash: str
    title: str
    unit_count: int
    chunks: tuple[DocumentChunk, ...]
    assets: tuple[DocumentAsset, ...]


def detect_document_type(data: bytes, filename: str) -> DocumentType:
    suffix = PurePosixPath(filename.lower()).suffix
    if suffix == ".doc":
        raise ValueError("不支持旧式 .doc；请另存为 .docx 后再导入")
    if suffix not in {".pdf", ".docx"}:
        raise ValueError("KP 本只接受 PDF 或 DOCX")
    if not data or len(data) > MAX_MODULE_BYTES:
        raise ValueError("KP 本为空或超过 64 MiB 限制")
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("文件扩展名为 PDF，但文件签名不正确")
        return "pdf"
    if not data.startswith(b"PK"):
        raise ValueError("文件扩展名为 DOCX，但文件签名不正确")
    try:
        with ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
    except BadZipFile as exc:
        raise ValueError("无法解析 DOCX 容器") from exc
    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise ValueError("上传文件不是有效的 Word DOCX 文档")
    return "docx"


def extract_module_document(
    data: bytes,
    filename: str,
    *,
    title: str,
) -> ExtractedModuleDocument:
    source_type = detect_document_type(data, filename)
    normalized_title = title.strip() or PurePosixPath(filename).stem
    if not normalized_title:
        raise ValueError("KP 本标题不能为空")
    source_hash = hashlib.sha256(data).hexdigest()
    if source_type == "pdf":
        return _extract_pdf(data, normalized_title, source_hash)
    return _extract_docx(data, normalized_title, source_hash)


def _extract_pdf(
    data: bytes,
    title: str,
    source_hash: str,
) -> ExtractedModuleDocument:
    try:
        reader = PdfReader(BytesIO(data), strict=True)
    except Exception as exc:
        raise ValueError("无法解析 KP 本 PDF") from exc
    if reader.is_encrypted:
        raise ValueError("不接受加密的 KP 本 PDF")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"KP 本超过 {MAX_PDF_PAGES} 页限制")

    chunks: list[DocumentChunk] = []
    assets: list[DocumentAsset] = []
    extracted_characters = 0
    asset_bytes = 0
    image_pixels = 0
    current_heading = title
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            raise ValueError(f"第 {page_number} 页文本提取失败") from exc
        for block in _split_pdf_text(page_text):
            extracted_characters += len(block)
            if extracted_characters > MAX_EXTRACTED_CHARACTERS:
                raise ValueError("KP 本提取文本超过 1200 万字符限制")
            possible_heading = _possible_heading(block)
            if possible_heading:
                current_heading = possible_heading
            hint = infer_semantic_kind(block)
            chunks.append(
                DocumentChunk(
                    title=current_heading,
                    text=block,
                    order_index=len(chunks),
                    page_start=page_number,
                    page_end=page_number,
                    source_locator=f"pdf:page:{page_number}",
                    semantic_kind=hint.semantic_kind,
                    classification_confidence=hint.confidence,
                    review_flags=hint.review_flags,
                )
            )
        try:
            page_images = list(page.images)
        except Exception as exc:
            raise ValueError(f"第 {page_number} 页图片提取失败") from exc
        for image_index, image in enumerate(page_images, start=1):
            raw = bytes(image.data)
            width = getattr(image.image, "width", None)
            height = getattr(image.image, "height", None)
            if width and height and width <= 32 and height <= 32:
                continue
            asset_bytes, image_pixels = _track_asset_limits(
                raw,
                width,
                height,
                count=len(assets) + 1,
                asset_bytes=asset_bytes,
                image_pixels=image_pixels,
            )
            filename = image.name or f"page-{page_number}-image-{image_index}.bin"
            role, confidence, flags = infer_asset_role(
                filename=filename,
                nearby_heading=current_heading,
                width=width,
                height=height,
            )
            assets.append(
                DocumentAsset(
                    data=raw,
                    filename=PurePosixPath(filename).name,
                    mime_type=_asset_mime(filename),
                    width=width,
                    height=height,
                    source_locator=f"pdf:page:{page_number}:image:{image_index}",
                    nearby_heading=current_heading,
                    asset_role=role,
                    classification_confidence=confidence,
                    review_flags=flags,
                )
            )
    if not chunks and not assets:
        raise ValueError("KP 本 PDF 没有可提取的文本或图片")
    return ExtractedModuleDocument(
        source_type="pdf",
        source_hash=source_hash,
        title=title,
        unit_count=len(reader.pages),
        chunks=tuple(chunks),
        assets=tuple(assets),
    )


def _extract_docx(
    data: bytes,
    title: str,
    source_hash: str,
) -> ExtractedModuleDocument:
    try:
        archive = ZipFile(BytesIO(data))
    except BadZipFile as exc:
        raise ValueError("无法解析 KP 本 DOCX") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("DOCX 包含过多文件")
        if sum(item.file_size for item in infos) > MAX_DOCX_EXPANDED_BYTES:
            raise ValueError("DOCX 解压后超过 256 MiB 限制")
        for item in infos:
            path = PurePosixPath(item.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in item.filename:
                raise ValueError("DOCX 包含不安全的内部路径")

        document_xml = _read_safe_xml(archive, "word/document.xml")
        relationships = _docx_relationships(archive)
        root = _parse_safe_xml(document_xml, "Word 正文")
        body = root.find(f"{_W}body")
        if body is None:
            raise ValueError("DOCX 缺少正文")

        chunks: list[DocumentChunk] = []
        assets: list[DocumentAsset] = []
        extracted_characters = 0
        asset_bytes = 0
        image_pixels = 0
        current_heading = title
        paragraph_index = 0
        referenced_media: set[str] = set()

        def append_asset(
            target: str,
            locator: str,
            heading: str,
        ) -> None:
            nonlocal asset_bytes, image_pixels
            raw = archive.read(target)
            width, height = _image_dimensions(raw)
            asset_bytes, image_pixels = _track_asset_limits(
                raw,
                width,
                height,
                count=len(assets) + 1,
                asset_bytes=asset_bytes,
                image_pixels=image_pixels,
            )
            referenced_media.add(target)
            filename = PurePosixPath(target).name
            role, confidence, flags = infer_asset_role(
                filename=filename,
                nearby_heading=heading,
                width=width,
                height=height,
            )
            assets.append(
                DocumentAsset(
                    data=raw,
                    filename=filename,
                    mime_type=_asset_mime(target),
                    width=width,
                    height=height,
                    source_locator=locator,
                    nearby_heading=heading,
                    asset_role=role,
                    classification_confidence=confidence,
                    review_flags=flags,
                )
            )

        for element in body:
            if element.tag == f"{_W}p":
                paragraph_index += 1
                text, style_annotations = _paragraph_text_and_styles(element)
                style = element.find(f"./{_W}pPr/{_W}pStyle")
                style_name = style.get(f"{_W}val", "") if style is not None else ""
                styled_heading = _is_heading_style(style_name)
                if text and looks_like_heading(text, styled_heading=styled_heading):
                    current_heading = text[:200]
                if text:
                    extracted_characters += len(text)
                    if extracted_characters > MAX_EXTRACTED_CHARACTERS:
                        raise ValueError("KP 本提取文本超过 1200 万字符限制")
                    hint = infer_semantic_kind(
                        text,
                        styled_heading=styled_heading,
                    )
                    chunks.append(
                        DocumentChunk(
                            title=current_heading,
                            text=text,
                            order_index=len(chunks),
                            paragraph_start=paragraph_index,
                            paragraph_end=paragraph_index,
                            source_locator=f"docx:paragraph:{paragraph_index}",
                            semantic_kind=hint.semantic_kind,
                            classification_confidence=hint.confidence,
                            style_annotations=style_annotations,
                            review_flags=hint.review_flags,
                        )
                    )
                for image_index, blip in enumerate(
                    element.iter(f"{_A}blip"),
                    start=1,
                ):
                    relationship_id = blip.get(f"{_R}embed")
                    target = relationships.get(relationship_id or "")
                    if not target:
                        continue
                    append_asset(
                        target,
                        f"docx:paragraph:{paragraph_index}:image:{image_index}",
                        current_heading,
                    )
            elif element.tag == f"{_W}tbl":
                paragraph_index += 1
                rows = []
                for row in element.findall(f"./{_W}tr"):
                    cells = [_element_text(cell) for cell in row.findall(f"./{_W}tc")]
                    if any(cells):
                        rows.append(" | ".join(cells))
                text = "\n".join(rows).strip()
                if text:
                    extracted_characters += len(text)
                    if extracted_characters > MAX_EXTRACTED_CHARACTERS:
                        raise ValueError("KP 本提取文本超过 1200 万字符限制")
                    hint = infer_semantic_kind(text, content_kind="table")
                    chunks.append(
                        DocumentChunk(
                            title=current_heading,
                            text=text,
                            order_index=len(chunks),
                            content_kind="table",
                            paragraph_start=paragraph_index,
                            paragraph_end=paragraph_index,
                            source_locator=f"docx:table:{paragraph_index}",
                            semantic_kind=hint.semantic_kind,
                            classification_confidence=hint.confidence,
                        )
                    )
                for image_index, blip in enumerate(element.iter(f"{_A}blip"), start=1):
                    target = relationships.get(blip.get(f"{_R}embed") or "")
                    if target:
                        append_asset(
                            target,
                            f"docx:table:{paragraph_index}:image:{image_index}",
                            current_heading,
                        )
        for name in sorted(archive.namelist()):
            if name.startswith("word/media/") and name not in referenced_media:
                append_asset(
                    name,
                    f"docx:media:{PurePosixPath(name).name}",
                    current_heading,
                )
        if not chunks and not assets:
            raise ValueError("KP 本 DOCX 没有可提取的正文、表格或图片")
        return ExtractedModuleDocument(
            source_type="docx",
            source_hash=source_hash,
            title=title,
            unit_count=paragraph_index,
            chunks=tuple(chunks),
            assets=tuple(assets),
        )


def _read_safe_xml(archive: ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise ValueError(f"DOCX 缺少 {name}") from exc
    if info.file_size > MAX_DOCX_XML_BYTES:
        raise ValueError(f"DOCX 的 {name} 超过 32 MiB 限制")
    return archive.read(info)


def _parse_safe_xml(data: bytes, label: str) -> ElementTree.Element:
    if _UNSAFE_XML.search(data):
        raise ValueError(f"{label}包含不允许的 XML 声明")
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise ValueError(f"{label} XML 无法解析") from exc


def _docx_relationships(archive: ZipFile) -> dict[str, str]:
    name = "word/_rels/document.xml.rels"
    if name not in archive.namelist():
        return {}
    root = _parse_safe_xml(_read_safe_xml(archive, name), "Word 关系")
    result: dict[str, str] = {}
    for relation in root.findall(f"{_PR}Relationship"):
        relation_id = relation.get("Id", "")
        target = relation.get("Target", "")
        if relation.get("TargetMode") == "External":
            continue
        normalized_text = posixpath.normpath(posixpath.join("word", target))
        normalized = PurePosixPath(normalized_text)
        if normalized.is_absolute() or not normalized_text.startswith("word/media/"):
            continue
        if str(normalized) in archive.namelist():
            result[relation_id] = str(normalized)
    return result


def _element_text(element: ElementTree.Element) -> str:
    return _normalize_text("".join(node.text or "" for node in element.iter(f"{_W}t")))


def _paragraph_text_and_styles(
    paragraph: ElementTree.Element,
) -> tuple[str, tuple[str, ...]]:
    annotations: set[str] = set()
    text_parts: list[str] = []
    for run in paragraph.findall(f"./{_W}r"):
        run_text = "".join(node.text or "" for node in run.iter(f"{_W}t"))
        text_parts.append(run_text)
        properties = run.find(f"{_W}rPr")
        if properties is None or not run_text.strip():
            continue
        for tag, label in (("b", "bold"), ("i", "italic"), ("u", "underline")):
            node = properties.find(f"{_W}{tag}")
            if node is not None and node.get(f"{_W}val", "true").casefold() not in {
                "0",
                "false",
                "none",
            }:
                annotations.add(label)
        color = properties.find(f"{_W}color")
        color_value = color.get(f"{_W}val", "") if color is not None else ""
        if color_value and color_value.casefold() not in {"auto", "000000"}:
            annotations.add(f"color:#{color_value.upper()}")
        highlight = properties.find(f"{_W}highlight")
        highlight_value = highlight.get(f"{_W}val", "") if highlight is not None else ""
        if highlight_value and highlight_value.casefold() not in {"none", "auto"}:
            annotations.add(f"highlight:{highlight_value.casefold()}")
    return _normalize_text("".join(text_parts)), tuple(sorted(annotations))


def _normalize_text(text: str) -> str:
    return "\n".join(
        line
        for line in (
            re.sub(r"[ \t\u00a0]+", " ", raw).strip()
            for raw in text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        )
        if line
    )


def _split_text(text: str) -> list[str]:
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


def _split_pdf_text(text: str) -> list[str]:
    """Keep page-local provenance while exposing headings and paragraph boundaries."""

    if not text.strip():
        return []
    paragraphs: list[str] = []
    current: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = re.sub(r"[ \t\u00a0]+", " ", raw_line).strip()
        if not line:
            if current:
                paragraphs.append("\n".join(current))
                current = []
            continue
        if looks_like_heading(line):
            if current:
                paragraphs.append("\n".join(current))
                current = []
            paragraphs.append(line)
            continue
        current.append(line)
    if current:
        paragraphs.append("\n".join(current))

    blocks: list[str] = []
    pending: list[str] = []
    pending_size = 0
    target_size = min(MAX_CHUNK_CHARACTERS, 1400)
    for paragraph in paragraphs:
        if looks_like_heading(paragraph):
            if pending:
                blocks.append("\n\n".join(pending))
                pending = []
                pending_size = 0
            blocks.append(paragraph)
            continue
        if pending and pending_size + len(paragraph) + 2 > target_size:
            blocks.append("\n\n".join(pending))
            pending = []
            pending_size = 0
        pending.append(paragraph)
        pending_size += len(paragraph) + 2
    if pending:
        blocks.append("\n\n".join(pending))
    return [block for block in blocks if block.strip()]


def _possible_heading(block: str) -> str | None:
    first = block.splitlines()[0].strip()
    if looks_like_heading(first):
        return first
    return None


def _is_heading_style(style: str) -> bool:
    normalized = style.casefold()
    return normalized.startswith(("heading", "title", "标题"))


def _asset_mime(filename: str) -> str:
    guessed = mimetypes.guess_type(filename)[0]
    return guessed if guessed in SAFE_RASTER_MIME_TYPES else "application/octet-stream"


def _image_dimensions(data: bytes) -> tuple[int | None, int | None]:
    try:
        from PIL import Image, UnidentifiedImageError

        with Image.open(BytesIO(data)) as image:
            return int(image.width), int(image.height)
    except (OSError, UnidentifiedImageError):
        return None, None


def _track_asset_limits(
    data: bytes,
    width: int | None,
    height: int | None,
    *,
    count: int,
    asset_bytes: int,
    image_pixels: int,
) -> tuple[int, int]:
    if count > MAX_ASSETS:
        raise ValueError(f"KP 本内嵌图片超过 {MAX_ASSETS} 张限制")
    if len(data) > MAX_SINGLE_ASSET_BYTES:
        raise ValueError("KP 本存在超过 32 MiB 的单张图片")
    asset_bytes += len(data)
    if asset_bytes > MAX_TOTAL_ASSET_BYTES:
        raise ValueError("KP 本图片总大小超过 256 MiB 限制")
    if width and height:
        pixels = width * height
        if pixels > MAX_SINGLE_IMAGE_PIXELS:
            raise ValueError("KP 本存在超过 5000 万像素的单张图片")
        image_pixels += pixels
        if image_pixels > MAX_TOTAL_IMAGE_PIXELS:
            raise ValueError("KP 本图片总像素超过 2.5 亿限制")
    return asset_bytes, image_pixels
