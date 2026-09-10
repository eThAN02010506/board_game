from __future__ import annotations

import pytest

from ai_kp.platform.modules.documents import (
    DocumentAsset,
    DocumentChunk,
    ExtractedModuleDocument,
)
from ai_kp.platform.modules.pdf_quality import assess_native_pdf


def _pdf(
    *,
    pages: int,
    text_by_page: dict[int, str],
    asset_pages: tuple[int, ...] = (),
) -> ExtractedModuleDocument:
    chunks = tuple(
        DocumentChunk(
            title="Scenario",
            text=text,
            order_index=index,
            page_start=page,
            page_end=page,
            source_locator=f"pdf:page:{page}",
        )
        for index, (page, text) in enumerate(sorted(text_by_page.items()))
    )
    assets = tuple(
        DocumentAsset(
            data=b"image",
            filename=f"page-{page}.png",
            mime_type="image/png",
            source_locator=f"pdf:page:{page}:image:1",
        )
        for page in asset_pages
    )
    return ExtractedModuleDocument(
        source_type="pdf",
        source_hash="a" * 64,
        title="Scenario",
        unit_count=pages,
        chunks=chunks,
        assets=assets,
    )


def test_native_pdf_quality_accepts_dense_text_without_assets() -> None:
    document = _pdf(
        pages=3,
        text_by_page={page: "调查线索" * 30 for page in range(1, 4)},
    )

    quality = assess_native_pdf(document)

    assert quality.accepted is True
    assert quality.reasons == ()
    assert quality.text_pages == 3


def test_native_pdf_quality_routes_scanned_pages_to_fallback() -> None:
    document = _pdf(pages=3, text_by_page={}, asset_pages=(1, 2, 3))

    quality = assess_native_pdf(document)

    assert quality.accepted is False
    assert quality.reasons == (
        "native_text_too_sparse",
        "native_page_coverage_low",
    )
    assert quality.asset_pages == 3


def test_native_pdf_quality_allows_one_image_cover_with_text_body() -> None:
    document = _pdf(
        pages=10,
        text_by_page={page: "正文证据" * 30 for page in range(2, 11)},
        asset_pages=(1,),
    )

    assert assess_native_pdf(document).accepted is True


def test_native_pdf_quality_rejects_corrupt_character_stream() -> None:
    document = _pdf(
        pages=1,
        text_by_page={1: ("有效原文" * 20) + ("\ufffd" * 10)},
    )

    quality = assess_native_pdf(document)

    assert quality.accepted is False
    assert "native_text_corrupt" in quality.reasons


def test_native_pdf_quality_rejects_non_pdf_contract() -> None:
    document = ExtractedModuleDocument(
        source_type="docx",
        source_hash="b" * 64,
        title="Scenario",
        unit_count=1,
        chunks=(),
        assets=(),
    )

    with pytest.raises(ValueError, match="only assess PDF"):
        assess_native_pdf(document)
