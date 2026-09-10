from __future__ import annotations

from pathlib import Path

import pytest

from ai_kp.infrastructure.modules.document_sandbox import (
    DocumentParsePolicy,
    _parse_with_policy,
)
from ai_kp.platform.modules.documents import (
    DocumentAsset,
    DocumentChunk,
    ExtractedModuleDocument,
)


def _document(*, text: str = "", with_asset: bool = False) -> ExtractedModuleDocument:
    chunks = (
        (
            DocumentChunk(
                title="Scenario",
                text=text,
                order_index=0,
                page_start=1,
                page_end=1,
                source_locator="pdf:page:1",
            ),
        )
        if text
        else ()
    )
    assets = (
        (
            DocumentAsset(
                data=b"image",
                filename="scan.png",
                mime_type="image/png",
                source_locator="pdf:page:1:image:1",
            ),
        )
        if with_asset
        else ()
    )
    return ExtractedModuleDocument(
        source_type="pdf",
        source_hash="a" * 64,
        title="Scenario",
        unit_count=1,
        chunks=chunks,
        assets=assets,
    )


def test_native_first_keeps_usable_native_pdf_without_mineru(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    native = _document(text="调查员能够取得两条彼此独立的线索。" * 10)
    monkeypatch.setattr(
        "ai_kp.platform.modules.documents.extract_module_document",
        lambda *_args, **_kwargs: native,
    )
    monkeypatch.setattr(
        "ai_kp.infrastructure.modules.document_sandbox._parse_mineru",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("MinerU must not run")),
    )

    result = _parse_with_policy(
        b"pdf",
        "scenario.pdf",
        result_root=tmp_path,
        title="Scenario",
        source_hash=native.source_hash,
        policy=DocumentParsePolicy(),
    )

    assert result is native


def test_native_first_falls_back_and_records_reasons(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    native = _document(with_asset=True)
    fallback = _document(text="OCR 恢复的正文" * 20, with_asset=True)
    monkeypatch.setattr(
        "ai_kp.platform.modules.documents.extract_module_document",
        lambda *_args, **_kwargs: native,
    )
    monkeypatch.setattr(
        "ai_kp.infrastructure.modules.document_sandbox._parse_mineru",
        lambda *_args, **_kwargs: fallback,
    )

    result = _parse_with_policy(
        b"pdf",
        "scan.pdf",
        result_root=tmp_path,
        title="Scenario",
        source_hash=native.source_hash,
        policy=DocumentParsePolicy(),
    )

    assert result.chunks[0].review_flags[:3] == (
        "native_pdf_fallback",
        "native_text_too_sparse",
        "native_page_coverage_low",
    )
    assert result.assets[0].review_flags[:3] == result.chunks[0].review_flags[:3]


def test_native_first_does_not_bypass_native_security_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "ai_kp.platform.modules.documents.extract_module_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("内容流超过安全限制")),
    )
    monkeypatch.setattr(
        "ai_kp.infrastructure.modules.document_sandbox._parse_mineru",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("security failures must fail closed")
        ),
    )

    with pytest.raises(ValueError, match="内容流超过安全限制"):
        _parse_with_policy(
            b"pdf",
            "unsafe.pdf",
            result_root=tmp_path,
            title="Scenario",
            source_hash="a" * 64,
            policy=DocumentParsePolicy(),
        )
