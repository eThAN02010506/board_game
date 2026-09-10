"""Deterministic quality gate for the lightweight native PDF parser."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ai_kp.platform.modules.documents import ExtractedModuleDocument

_PDF_PAGE_LOCATOR = re.compile(r"^(?:pdf|mineru):page:(\d+)(?::|$)")


@dataclass(frozen=True)
class NativePdfQuality:
    """Bounded routing evidence; reason codes are safe to persist or display."""

    accepted: bool
    reasons: tuple[str, ...]
    text_characters: int
    text_pages: int
    asset_pages: int
    suspicious_characters: int


def assess_native_pdf(document: ExtractedModuleDocument) -> NativePdfQuality:
    """Decide whether native extraction is complete enough for scenario ingestion.

    This deliberately evaluates extraction integrity, not prose quality. A lack
    of guessed headings is reviewable; missing or corrupt text is a parser route.
    """

    if document.source_type != "pdf":
        raise ValueError("Native PDF quality can only assess PDF documents")

    page_text: dict[int, list[str]] = {}
    for chunk in document.chunks:
        page = chunk.page_start
        if page is None or page < 1:
            continue
        page_text.setdefault(page, []).append(chunk.text)
    text_by_page = {
        page: "".join(part for part in parts if part) for page, parts in page_text.items()
    }
    text_characters = sum(
        1 for text in text_by_page.values() for character in text if not character.isspace()
    )
    suspicious_characters = sum(
        1
        for text in text_by_page.values()
        for character in text
        if _is_suspicious_character(character)
    )
    text_pages = {
        page
        for page, text in text_by_page.items()
        if sum(not character.isspace() for character in text) >= 20
    }
    asset_pages = {
        page
        for asset in document.assets
        if (page := _locator_page(asset.source_locator)) is not None
    }
    meaningful_pages = text_pages | asset_pages

    reasons: list[str] = []
    minimum_characters = max(20, document.unit_count * 40)
    if text_characters < minimum_characters:
        reasons.append("native_text_too_sparse")
    if meaningful_pages and len(text_pages) / len(meaningful_pages) < 0.7:
        reasons.append("native_page_coverage_low")
    if text_characters and suspicious_characters / text_characters > 0.02:
        reasons.append("native_text_corrupt")

    return NativePdfQuality(
        accepted=not reasons,
        reasons=tuple(reasons),
        text_characters=text_characters,
        text_pages=len(text_pages),
        asset_pages=len(asset_pages),
        suspicious_characters=suspicious_characters,
    )


def _locator_page(locator: str) -> int | None:
    match = _PDF_PAGE_LOCATOR.match(locator)
    return int(match.group(1)) if match is not None else None


def _is_suspicious_character(character: str) -> bool:
    if character == "\ufffd":
        return True
    codepoint = ord(character)
    if 0xE000 <= codepoint <= 0xF8FF:
        return True
    if 0xF0000 <= codepoint <= 0xFFFFD:
        return True
    if 0x100000 <= codepoint <= 0x10FFFD:
        return True
    return unicodedata.category(character) in {"Cc", "Cs"} and character not in {
        "\n",
        "\r",
        "\t",
    }


__all__ = ["NativePdfQuality", "assess_native_pdf"]
