from __future__ import annotations

import hashlib
import os
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.document_process_sandbox import (
    DocumentProcessPolicy,
    DocumentProcessSandboxError,
    run_document_process_isolated,
)
from ai_kp.infrastructure.knowledge.rulebook_sandbox import (
    RulebookDocumentSandboxError,
    extract_rulebook_pdf_isolated,
)


def _text_pdf() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 72 720 Td "
        b"(6.1 Skill check succeeds on 50 or less.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(output)
    return output.getvalue()


def _compressed_content_stream_pdf() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(b" " * (20 * 1024 * 1024))
    page.replace_contents(stream.flate_encode())
    writer.write(output)
    data = output.getvalue()
    assert len(data) < 25 * 1024
    return data


def _crashing_child(
    _result_root: str,
    _source_path: str,
    _policy: DocumentProcessPolicy,
) -> None:
    os._exit(7)


def test_rulebook_pdf_extraction_runs_in_fresh_spawn_and_preserves_hash() -> None:
    data = _text_pdf()
    policy = DocumentProcessPolicy(timeout_seconds=5)

    with patch(
        "ai_kp.infrastructure.knowledge.rulebook_sandbox.extract_rulebook_pdf",
        side_effect=AssertionError("main process parser must not run"),
    ):
        extracted = extract_rulebook_pdf_isolated(
            data,
            "rules.pdf",
            policy=policy,
        )

    assert extracted.source_hash == hashlib.sha256(data).hexdigest()
    assert extracted.page_count == 1
    assert extracted.chunks[0]["audience"] == "kp"
    assert "Skill check succeeds" in extracted.chunks[0]["text"]


def test_rulebook_pdf_sandbox_times_out_and_reports_child_parse_failure() -> None:
    data = _text_pdf()
    with pytest.raises(RulebookDocumentSandboxError, match="解析超过"):
        extract_rulebook_pdf_isolated(
            data,
            "timeout.pdf",
            policy=DocumentProcessPolicy(timeout_seconds=0.000001),
        )

    with pytest.raises(RulebookDocumentSandboxError, match="无法解析规则书 PDF"):
        extract_rulebook_pdf_isolated(
            b"%PDF-this-is-not-a-real-document",
            "broken.pdf",
            policy=DocumentProcessPolicy(timeout_seconds=5),
        )


def test_document_process_sandbox_reports_abnormal_child_exit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(_text_pdf())
    with pytest.raises(DocumentProcessSandboxError, match="代码 7"):
        run_document_process_isolated(
            source,
            operation_label="规则书 PDF 解析",
            # ``spawn`` imports the test module in a clean interpreter. Under the
            # full suite that startup can exceed five seconds before this target
            # immediately exits, so keep this assertion about the exit code rather
            # than accidentally turning it into another timeout test.
            policy=DocumentProcessPolicy(timeout_seconds=15),
            target=_crashing_child,
            child_args=(),
            read_result=lambda _result_root: None,
        )
    assert list(tmp_path.glob(".document-parse-*")) == []


def test_rulebook_api_rejects_compressed_stream_and_still_imports_normal_pdf(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "rulebook-sandbox.sqlite3",
        rulebook_index_root=tmp_path / "rule-index",
        module_asset_root=tmp_path / "module-assets",
        module_parse_timeout_seconds=10,
        local_admin_enabled=False,
        admin_token="rulebook-admin",
    )
    app = create_app(settings)
    admin_headers = {"X-AI-KP-Admin-Token": "rulebook-admin"}
    with TestClient(app) as client:
        compressed = client.post(
            "/rulebooks/sources",
            headers={**admin_headers, "X-File-Name": "compressed.pdf"},
            content=_compressed_content_stream_pdf(),
        )
        assert compressed.status_code == 409
        assert "解压内容超过 16 MiB" in compressed.json()["detail"]
        assert client.get("/health").status_code == 200

        data = _text_pdf()
        imported = client.post(
            "/rulebooks/sources",
            headers={**admin_headers, "X-File-Name": "rules.pdf"},
            content=data,
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["source_hash"] == hashlib.sha256(data).hexdigest()
        assert imported.json()["page_count"] == 1
        assert imported.json()["chunk_count"] == 1
        assert imported.json()["status"] == "extracted"
