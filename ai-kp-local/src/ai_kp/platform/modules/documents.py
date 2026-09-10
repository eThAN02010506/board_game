"""Bounded PDF and DOCX extraction with stable source provenance."""

from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import re
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Literal
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader

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

DocumentType = Literal["pdf", "docx"]
PARSER_VERSION = "module-document.v9"
MAX_MODULE_BYTES = 64 * 1024 * 1024
OLE_CFB_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_PDF_PAGES = 1200
MAX_PDF_PAGE_CONTENT_BYTES = 16 * 1024 * 1024
MAX_PDF_TOTAL_CONTENT_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_DOCX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_DOCX_XML_BYTES = 32 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 12_000_000
MAX_DOCUMENT_CHUNKS = 100_000
MAX_CHUNK_CHARACTERS = 2600
MAX_ASSETS = 500
MAX_PDF_IMAGE_OBJECTS = 2_000
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
_PDF_PAGE_LABEL = re.compile(r"^\d{1,4}$")
_PDF_COLON_HEADING = re.compile(
    r"^(?:展示材料|附录\s*[A-Z一二三四五六七八九十0-9]*|"
    r"给守秘人的建议|.+的启示)\s*[:：]",
    re.IGNORECASE,
)
_PDF_ALWAYS_HEADINGS = {
    "引言",
    "战斗",
    "技能",
    "奖励",
    "特殊能力",
}


@dataclass(frozen=True)
class _PdfTextBlock:
    text: str
    is_heading: bool = False
    layout_inferred_heading: bool = False


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
    heading_level: int | None = None
    section_path: tuple[str, ...] = ()
    scene_key: str | None = None


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
    if suffix not in {".pdf", ".doc", ".docx"}:
        raise ValueError("KP 本只接受 PDF、DOC 或 DOCX")
    if not data or len(data) > MAX_MODULE_BYTES:
        raise ValueError("KP 本为空或超过 64 MiB 限制")
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("文件扩展名为 PDF，但文件签名不正确")
        return "pdf"
    if suffix == ".doc":
        if not data.startswith(OLE_CFB_SIGNATURE):
            raise ValueError("文件扩展名为 DOC，但 OLE CFB 文件签名不正确")
        # The durable schema intentionally normalizes converted legacy Word
        # sources to docx; the original .doc bytes and hash remain authoritative.
        return "docx"
    if not data.startswith(b"PK"):
        raise ValueError("文件扩展名为 DOCX，但文件签名不正确")
    return "docx"


def extract_module_document(
    data: bytes,
    filename: str,
    *,
    title: str,
) -> ExtractedModuleDocument:
    if PurePosixPath(filename.lower()).suffix == ".doc":
        raise ValueError("旧式 .doc 必须通过隔离的模组导入转换流程处理")
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
        # Real-world scenario PDFs are often structurally recoverable without
        # being fully spec-conformant. pypdf documents ``strict=False`` as its
        # best-effort mode; the parser sandbox and explicit resource limits
        # below remain the security boundary.
        reader = PdfReader(BytesIO(data), strict=False)
    except Exception as exc:
        raise ValueError("无法解析 KP 本 PDF") from exc
    if reader.is_encrypted:
        raise ValueError("不接受加密的 KP 本 PDF")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"KP 本超过 {MAX_PDF_PAGES} 页限制")

    page_texts: list[str] = []
    pdf_content_bytes = 0
    for page_number, page in enumerate(reader.pages, start=1):
        page_content_bytes = _pdf_page_content_bytes(page, page_number)
        if page_content_bytes > MAX_PDF_PAGE_CONTENT_BYTES:
            raise ValueError(
                f"第 {page_number} 页解压后的 PDF 内容流超过 16 MiB 限制"
            )
        pdf_content_bytes += page_content_bytes
        if pdf_content_bytes > MAX_PDF_TOTAL_CONTENT_BYTES:
            raise ValueError("KP 本解压后的 PDF 内容流总计超过 128 MiB 限制")
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            raise ValueError(f"第 {page_number} 页文本提取失败") from exc
        page_texts.append(page_text)

    repeated_boundary_lines = _repeated_pdf_boundary_lines(page_texts)
    numeric_boundary_edges = _numeric_pdf_boundary_edges(page_texts)
    chunks: list[DocumentChunk] = []
    assets: list[DocumentAsset] = []
    extracted_characters = 0
    asset_bytes = 0
    image_pixels = 0
    pdf_images_seen = 0
    current_heading = title
    section_headings: dict[int, str] = {}
    structural_parent_level: int | None = None
    for page_number, (page, page_text) in enumerate(
        zip(reader.pages, page_texts, strict=True),
        start=1,
    ):
        for block in _split_pdf_blocks(
            page_text,
            repeated_boundary_lines=repeated_boundary_lines,
            numeric_boundary_edges=numeric_boundary_edges,
        ):
            extracted_characters += len(block.text)
            if extracted_characters > MAX_EXTRACTED_CHARACTERS:
                raise ValueError("KP 本提取文本超过 1200 万字符限制")
            if block.is_heading:
                current_heading = block.text[:200]
            heading_level, structural_parent_level = resolve_heading_level(
                title=block.text if block.is_heading else "",
                is_heading=block.is_heading,
                explicit_level=None,
                structural_parent_level=structural_parent_level,
            )
            if heading_level is not None:
                section_headings = {
                    level: heading
                    for level, heading in section_headings.items()
                    if level < heading_level
                }
                section_headings[heading_level] = current_heading
            section_path = tuple(section_headings[level] for level in sorted(section_headings))
            hint = infer_semantic_kind(
                block.text,
                content_kind="heading" if block.is_heading else "text",
                styled_heading=block.is_heading,
            )
            hint = inherit_heading_semantics(hint, current_heading)
            if len(chunks) >= MAX_DOCUMENT_CHUNKS:
                raise ValueError(
                    f"KP 本提取文本块超过 {MAX_DOCUMENT_CHUNKS} 个限制"
                )
            chunks.append(
                DocumentChunk(
                    title=current_heading,
                    text=block.text,
                    order_index=len(chunks),
                    content_kind="heading" if block.is_heading else "text",
                    page_start=page_number,
                    page_end=page_number,
                    source_locator=f"pdf:page:{page_number}",
                    semantic_kind=hint.semantic_kind,
                    classification_confidence=hint.confidence,
                    review_flags=(
                        "builtin_pdf_text_layer",
                        *(
                            ("pdf_layout_inferred_heading",)
                            if block.layout_inferred_heading
                            else ()
                        ),
                        *hint.review_flags,
                    ),
                    heading_level=heading_level if block.is_heading else None,
                    section_path=section_path,
                    scene_key=inferred_scene_key(section_path),
                )
            )
        try:
            page_images = iter(page.images)
        except Exception as exc:
            raise ValueError(f"第 {page_number} 页图片提取失败") from exc
        image_index = 0
        while True:
            try:
                image = next(page_images)
            except StopIteration:
                break
            except Exception as exc:
                raise ValueError(f"第 {page_number} 页图片提取失败") from exc
            image_index += 1
            pdf_images_seen += 1
            if pdf_images_seen > MAX_PDF_IMAGE_OBJECTS:
                raise ValueError(
                    f"KP 本 PDF 图片对象超过 {MAX_PDF_IMAGE_OBJECTS} 个限制"
                )
            try:
                raw = bytes(image.data)
                width = getattr(image.image, "width", None)
                height = getattr(image.image, "height", None)
            except Exception as exc:
                raise ValueError(
                    f"第 {page_number} 页第 {image_index} 张图片提取失败"
                ) from exc
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
                    review_flags=("builtin_pdf_asset_extraction", *flags),
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
        names = {item.filename for item in infos}
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise ValueError("上传文件不是有效的 Word DOCX 文档")

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
        section_headings: dict[int, str] = {}
        structural_parent_level: int | None = None
        style_heading_levels = _docx_style_heading_levels(archive)
        paragraph_index = 0
        referenced_media: set[str] = set()

        def append_asset(
            target: str,
            locator: str,
            heading: str,
        ) -> None:
            nonlocal asset_bytes, image_pixels
            if len(assets) >= MAX_ASSETS:
                raise ValueError(f"KP 本内嵌图片超过 {MAX_ASSETS} 张限制")
            try:
                info = archive.getinfo(target)
            except KeyError as exc:
                raise ValueError(f"DOCX 缺少内嵌图片 {target}") from exc
            if info.file_size > MAX_SINGLE_ASSET_BYTES:
                raise ValueError("KP 本存在超过 32 MiB 的单张图片")
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
                explicit_heading_level = _docx_paragraph_heading_level(
                    element,
                    style_id=style_name,
                    style_heading_levels=style_heading_levels,
                )
                styled_heading = _is_heading_style(style_name) or _is_emphasized_heading(
                    text,
                    style_annotations,
                )
                is_heading = bool(
                    text and looks_like_heading(text, styled_heading=styled_heading)
                )
                heading_level, structural_parent_level = resolve_heading_level(
                    title=text,
                    is_heading=is_heading,
                    explicit_level=explicit_heading_level,
                    structural_parent_level=structural_parent_level,
                )
                if is_heading:
                    current_heading = text[:200]
                if text and heading_level is not None:
                    section_headings = {
                        level: heading
                        for level, heading in section_headings.items()
                        if level < heading_level
                    }
                    section_headings[heading_level] = text[:200]
                section_path = tuple(
                    section_headings[level] for level in sorted(section_headings)
                )
                if text:
                    extracted_characters += len(text)
                    if extracted_characters > MAX_EXTRACTED_CHARACTERS:
                        raise ValueError("KP 本提取文本超过 1200 万字符限制")
                    hint = infer_semantic_kind(
                        text,
                        styled_heading=styled_heading,
                    )
                    hint = inherit_heading_semantics(hint, current_heading)
                    if len(chunks) >= MAX_DOCUMENT_CHUNKS:
                        raise ValueError(
                            f"KP 本提取文本块超过 {MAX_DOCUMENT_CHUNKS} 个限制"
                        )
                    table_section_path = tuple(
                        section_headings[level]
                        for level in sorted(section_headings)
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
                            heading_level=heading_level,
                            section_path=section_path,
                            scene_key=inferred_scene_key(section_path),
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
                    if len(chunks) >= MAX_DOCUMENT_CHUNKS:
                        raise ValueError(
                            f"KP 本提取文本块超过 {MAX_DOCUMENT_CHUNKS} 个限制"
                        )
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
                            section_path=table_section_path,
                            scene_key=inferred_scene_key(table_section_path),
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


def _pdf_page_content_bytes(page: object, page_number: int) -> int:
    try:
        contents = page.get_contents()
        if contents is None:
            return 0
        return len(contents.get_data())
    except Exception as exc:
        raise ValueError(f"第 {page_number} 页 PDF 内容流解压失败") from exc


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


def _split_pdf_blocks(
    text: str,
    *,
    repeated_boundary_lines: frozenset[str] = frozenset(),
    numeric_boundary_edges: frozenset[str] = frozenset(),
) -> list[_PdfTextBlock]:
    """Split native text while retaining conservative, auditable headings."""

    lines = _clean_pdf_lines(
        text,
        repeated_boundary_lines=repeated_boundary_lines,
        numeric_boundary_edges=numeric_boundary_edges,
    )
    if not any(lines):
        return []
    heading_lines = _pdf_heading_lines(lines)
    paragraphs: list[_PdfTextBlock] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            paragraphs.append(_PdfTextBlock("\n".join(current)))
            current.clear()

    for line_index, line in enumerate(lines):
        if not line:
            flush()
            continue
        heading = heading_lines.get(line_index)
        if heading is not None:
            flush()
            paragraphs.append(
                _PdfTextBlock(
                    line,
                    is_heading=True,
                    layout_inferred_heading=heading,
                )
            )
            continue
        current.append(line)
    flush()

    blocks: list[_PdfTextBlock] = []
    pending: list[str] = []
    pending_size = 0
    target_size = min(MAX_CHUNK_CHARACTERS, 1400)
    for paragraph in paragraphs:
        if paragraph.is_heading:
            if pending:
                blocks.append(_PdfTextBlock("\n\n".join(pending)))
                pending = []
                pending_size = 0
            blocks.append(paragraph)
            continue
        if pending and pending_size + len(paragraph.text) + 2 > target_size:
            blocks.append(_PdfTextBlock("\n\n".join(pending)))
            pending = []
            pending_size = 0
        pending.append(paragraph.text)
        pending_size += len(paragraph.text) + 2
    if pending:
        blocks.append(_PdfTextBlock("\n\n".join(pending)))
    return [block for block in blocks if block.text.strip()]


def _clean_pdf_lines(
    text: str,
    *,
    repeated_boundary_lines: frozenset[str],
    numeric_boundary_edges: frozenset[str],
) -> list[str]:
    lines = [
        re.sub(r"[ \t\u00a0]+", " ", raw_line).strip()
        for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    ]
    nonempty = [index for index, line in enumerate(lines) if line]
    leading = frozenset(nonempty[:3])
    trailing = frozenset(nonempty[-3:])
    for index in leading | trailing:
        line = lines[index]
        if line in repeated_boundary_lines or (
            _PDF_PAGE_LABEL.fullmatch(line)
            and (
                (index in leading and "leading" in numeric_boundary_edges)
                or (index in trailing and "trailing" in numeric_boundary_edges)
            )
        ):
            lines[index] = ""
    return lines


def _pdf_heading_lines(lines: list[str]) -> dict[int, bool]:
    nonempty = [index for index, line in enumerate(lines) if line]
    result: dict[int, bool] = {}
    for position, index in enumerate(nonempty):
        line = lines[index]
        if looks_like_heading(line):
            result[index] = False
            continue
        previous = lines[nonempty[position - 1]] if position else ""
        following = lines[nonempty[position + 1]] if position + 1 < len(nonempty) else ""
        if _looks_like_pdf_layout_heading(
            line,
            previous=previous,
            following=following,
            starts_page=position == 0,
            follows_blank=index > 0 and not lines[index - 1],
        ):
            result[index] = True
    return result


def _looks_like_pdf_layout_heading(
    line: str,
    *,
    previous: str,
    following: str,
    starts_page: bool,
    follows_blank: bool,
) -> bool:
    normalized = " ".join(line.split()).strip()
    if not following or not 1 < len(normalized) <= 40:
        return False
    if _PDF_PAGE_LABEL.fullmatch(normalized):
        return False
    if normalized.startswith(("“", "‘", '"', "'", "（", "(")):
        return False
    if any(marker in normalized for marker in ("（", "）", "(", ")", "%")):
        return False
    if len(re.findall(r"\d+", normalized)) >= 2:
        return False
    if any(marker in normalized for marker in ("。", "！", "？", "?", "!", "；", ";", "，", ",")):
        return False
    if normalized.endswith(("、", "…")):
        return False
    normalized_folded = normalized.casefold()
    if normalized_folded in _PDF_ALWAYS_HEADINGS:
        return True
    if normalized.endswith(("：", ":")) and not _PDF_COLON_HEADING.match(normalized):
        return False
    if _PDF_COLON_HEADING.match(normalized):
        return True
    previous_ends_sentence = previous.rstrip().endswith(
        ("。", "！", "？", ".", "!", "?", "。”", "！”", "？”")
    )
    if not (starts_page or follows_blank or previous_ends_sentence):
        return False
    following_is_body = len(following) >= max(16, len(normalized) + 4) or any(
        marker in following for marker in ("。", "，", ",", ".", "：", ":")
    )
    return following_is_body


def _repeated_pdf_boundary_lines(page_texts: list[str]) -> frozenset[str]:
    if len(page_texts) < 3:
        return frozenset()
    counts: Counter[str] = Counter()
    for text in page_texts:
        lines = [
            re.sub(r"[ \t\u00a0]+", " ", raw).strip()
            for raw in text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
            if raw.strip()
        ]
        counts.update({*lines[:3], *lines[-3:]})
    threshold = max(3, (len(page_texts) * 3 + 4) // 5)
    return frozenset(
        line
        for line, count in counts.items()
        if line and not _PDF_PAGE_LABEL.fullmatch(line) and count >= threshold
    )


def _numeric_pdf_boundary_edges(page_texts: list[str]) -> frozenset[str]:
    if len(page_texts) < 3:
        return frozenset()
    counts = Counter[str]()
    for text in page_texts:
        lines = [
            re.sub(r"[ \t\u00a0]+", " ", raw).strip()
            for raw in text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
            if raw.strip()
        ]
        counts["leading"] += int(any(_PDF_PAGE_LABEL.fullmatch(line) for line in lines[:3]))
        counts["trailing"] += int(any(_PDF_PAGE_LABEL.fullmatch(line) for line in lines[-3:]))
    threshold = max(3, (len(page_texts) * 3 + 4) // 5)
    return frozenset(edge for edge, count in counts.items() if count >= threshold)


def _is_heading_style(style: str) -> bool:
    normalized = style.casefold()
    return normalized.startswith(("heading", "title", "标题"))


def _docx_paragraph_heading_level(
    paragraph: ElementTree.Element,
    *,
    style_id: str,
    style_heading_levels: dict[str, int],
) -> int | None:
    outline = paragraph.find(f"./{_W}pPr/{_W}outlineLvl")
    if outline is not None:
        value = outline.get(f"{_W}val", "")
        if value.isdigit() and 0 <= int(value) <= 8:
            return int(value) + 1
    if style_id in style_heading_levels:
        return style_heading_levels[style_id]
    match = re.fullmatch(
        r"(?:heading|标题)\s*([1-9])", style_id, re.IGNORECASE
    )
    return int(match.group(1)) if match else None


def _docx_style_heading_levels(archive: ZipFile) -> dict[str, int]:
    """Read only explicit Word outline metadata; visual guesses own no ancestry."""

    if "word/styles.xml" not in archive.namelist():
        return {}
    root = _parse_safe_xml(_read_safe_xml(archive, "word/styles.xml"), "Word 样式")
    levels: dict[str, int] = {}
    based_on: dict[str, str] = {}
    for style in root.findall(f".//{_W}style"):
        style_id = style.get(f"{_W}styleId", "")
        if not style_id:
            continue
        outline = style.find(f"./{_W}pPr/{_W}outlineLvl")
        value = outline.get(f"{_W}val", "") if outline is not None else ""
        if value.isdigit() and 0 <= int(value) <= 8:
            levels[style_id] = int(value) + 1
        else:
            name = style.find(f"./{_W}name")
            candidates = (style_id, name.get(f"{_W}val", "") if name is not None else "")
            for candidate in candidates:
                match = re.fullmatch(
                    r"(?:heading|标题)\s*([1-9])", candidate, re.IGNORECASE
                )
                if match:
                    levels[style_id] = int(match.group(1))
                    break
        parent = style.find(f"./{_W}basedOn")
        if parent is not None and parent.get(f"{_W}val"):
            based_on[style_id] = str(parent.get(f"{_W}val"))
    for _ in range(len(based_on)):
        changed = False
        for style_id, parent_id in based_on.items():
            if style_id not in levels and parent_id in levels:
                levels[style_id] = levels[parent_id]
                changed = True
        if not changed:
            break
    return levels


def _is_emphasized_heading(text: str, annotations: tuple[str, ...]) -> bool:
    normalized = " ".join(text.split()).strip()
    return bool(
        "bold" in annotations
        and 0 < len(normalized) <= 60
        and "\n" not in text
        and not normalized.endswith(("。", "！", "？", ".", "!", "?"))
    )


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
