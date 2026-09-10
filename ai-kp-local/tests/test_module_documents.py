from __future__ import annotations

import base64
import hashlib
import os
import time
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject

from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.infrastructure.modules.document_sandbox import (
    DocumentParsePolicy,
    ModuleDocumentSandboxError,
    extract_module_document_isolated,
)
from ai_kp.infrastructure.modules.import_worker import (
    ModuleImportWorker,
    enqueue_module_import,
)
from ai_kp.platform.modules.documents import (
    MAX_MODULE_BYTES,
    OLE_CFB_SIGNATURE,
    PARSER_VERSION,
    detect_document_type,
    extract_module_document,
)
from ai_kp.platform.modules.structure import infer_asset_role, infer_semantic_kind


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (40, 24), (83, 102, 91)).save(buffer, format="PNG")
    return buffer.getvalue()


def _docx(*, valid_xml: bool = True) -> bytes:
    document = (
        """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <w:body>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>第一章 雾港</w:t></w:r></w:p>
  <w:p><w:r><w:rPr><w:color w:val="C00000"/></w:rPr>
   <w:t>码头仓库中藏着一张旧照片。</w:t></w:r>
   <w:r><a:blip r:embed="rId5"/></w:r>
  </w:p>
  <w:tbl><w:tr><w:tc><w:p><w:r><w:t>人物</w:t></w:r></w:p></w:tc>
   <w:tc><w:p><w:r><w:t>秘密</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
 </w:body>
</w:document>""".encode()
        if valid_xml
        else b"<w:document"
    )
    relationships = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId5"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
  Target="media/clue.png"/>
</Relationships>"""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", relationships)
        archive.writestr("word/media/clue.png", _png())
    return output.getvalue()


def _legacy_doc() -> bytes:
    return OLE_CFB_SIGNATURE + (b"\x00" * 504)


def _ending_docx() -> bytes:
    document = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:body>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>结局</w:t></w:r></w:p>
  <w:p><w:r><w:t>若调查员消除了威胁，委托人会支付报酬。</w:t></w:r></w:p>
  <w:p><w:r><w:t>进行信用评级检定以确定额外报酬。</w:t></w:r></w:p>
 </w:body>
</w:document>""".encode()
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def _round69_heading_docx() -> bytes:
    headings = (
        "场景 2：调查开始",
        "查阅剪报",
        "一楼",
        "2 号房间: 儿童房",
        "床架攻击",
        "刀进行的攻击",
    )
    paragraphs = "".join(
        f"<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>{heading}</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>这是当前标题下的普通正文。</w:t></w:r></w:p>"
        for heading in headings
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}</w:body></w:document>"
    ).encode()
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def _fake_converter(tmp_path: Path, body: str) -> Path:
    converter = tmp_path / "fake-soffice"
    converter.write_text(
        "#!/usr/bin/env python3\n" + body,
        encoding="utf-8",
    )
    converter.chmod(0o700)
    return converter


def _successful_fake_converter(
    tmp_path: Path,
    *,
    argument_log: Path | None = None,
) -> Path:
    encoded_docx = base64.b64encode(_docx()).decode("ascii")
    log_statement = (
        f"Path({str(argument_log)!r}).write_text('\\n'.join(args), encoding='utf-8')"
        if argument_log is not None
        else "pass"
    )
    return _fake_converter(
        tmp_path,
        f"""import base64
import sys
from pathlib import Path

args = sys.argv[1:]
outdir = Path(args[args.index("--outdir") + 1])
source = Path(args[-1])
(outdir / f"{{source.stem}}.docx").write_bytes(
    base64.b64decode({encoded_docx!r})
)
{log_statement}
""",
    )


def _scanned_pdf() -> bytes:
    output = BytesIO()
    Image.new("RGB", (120, 80), (220, 210, 190)).save(output, format="PDF")
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


def _recoverable_non_strict_pdf() -> bytes:
    data = _scanned_pdf()
    marker = data.rfind(b"startxref\n") + len(b"startxref\n")
    end = data.find(b"\n", marker)
    startxref = int(data[marker:end])
    return data[:marker] + str(startxref + 1).encode() + data[end:]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _wait_for_import(
    client: TestClient,
    job_id: str,
    headers: dict[str, str],
    *,
    timeout: float = 20,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/module-imports/{job_id}", headers=headers)
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"module import {job_id} did not finish within {timeout} seconds")


def test_docx_extraction_preserves_heading_table_image_and_anchors() -> None:
    result = extract_module_document(_docx(), "雾港.docx", title="雾港疑云")

    assert result.source_type == "docx"
    assert [chunk.content_kind for chunk in result.chunks] == ["text", "text", "table"]
    assert result.chunks[1].title == "第一章 雾港"
    assert result.chunks[1].source_locator == "docx:paragraph:2"
    assert result.chunks[0].semantic_kind == "heading"
    assert result.chunks[0].heading_level == 1
    assert result.chunks[0].section_path == ("第一章 雾港",)
    assert result.chunks[1].heading_level is None
    assert result.chunks[1].section_path == ("第一章 雾港",)
    assert result.chunks[1].style_annotations == ("color:#C00000",)
    assert "人物 | 秘密" in result.chunks[2].text
    assert len(result.assets) == 1
    assert result.assets[0].source_locator == "docx:paragraph:2:image:1"
    assert result.assets[0].width == 40
    assert result.assets[0].height == 24


def test_docx_plain_prose_inherits_a_strong_ending_section() -> None:
    result = extract_module_document(_ending_docx(), "ending.docx", title="Scenario")

    assert [chunk.semantic_kind for chunk in result.chunks] == [
        "heading",
        "ending",
        "check",
    ]


def test_round69_style_headings_get_bounded_structural_ancestry() -> None:
    result = extract_module_document(
        _round69_heading_docx(), "round69.docx", title="Round 69"
    )
    heading_chunks = result.chunks[::2]

    assert [chunk.heading_level for chunk in heading_chunks] == [1, 2, 2, 3, 4, 4]
    assert heading_chunks[1].section_path == ("场景 2：调查开始", "查阅剪报")
    assert all(chunk.scene_key == "调查开始" for chunk in heading_chunks[1:])
    assert heading_chunks[4].section_path == (
        "场景 2：调查开始",
        "一楼",
        "2 号房间: 儿童房",
        "床架攻击",
    )
    assert heading_chunks[5].section_path[-1] == "刀进行的攻击"
    assert result.chunks[-1].heading_level is None


def test_round69_reversed_room_numbers_are_sibling_level_three_sections() -> None:
    headings = (
        "场景 3",
        "地下室",
        "1号储藏室",
        "房间 2: 科比特的藏身处",
        "房间3：儿童房",
        "房间4：浴室",
    )
    paragraphs = "".join(
        f"<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>{heading}</w:t></w:r></w:p>"
        for heading in headings
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}</w:body></w:document>"
    ).encode()
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)

    result = extract_module_document(output.getvalue(), "round69.docx", title="Round 69")

    assert [chunk.heading_level for chunk in result.chunks] == [1, 2, 3, 3, 3, 3]
    assert result.chunks[3].section_path == (
        "场景 3",
        "地下室",
        "房间 2: 科比特的藏身处",
    )
    assert result.chunks[4].section_path[-1] == "房间3：儿童房"
    assert "1号储藏室" not in result.chunks[5].section_path


def test_legacy_doc_detection_requires_exact_ole_signature_and_isolated_flow() -> None:
    data = _legacy_doc()

    assert detect_document_type(data, "常暗之厢.doc") == "docx"
    assert PARSER_VERSION == "module-document.v9"
    with pytest.raises(ValueError, match="OLE CFB"):
        detect_document_type(b"not-an-ole-document", "伪装.doc")
    with pytest.raises(ValueError, match="隔离"):
        extract_module_document(data, "常暗之厢.doc", title="常暗之厢")


@pytest.mark.skipif(os.name == "nt", reason="executable fixture uses a POSIX shebang")
def test_legacy_doc_import_reports_missing_converter(tmp_path: Path) -> None:
    data = _legacy_doc()
    source_path = tmp_path / "missing-converter.doc"
    source_path.write_bytes(data)

    with pytest.raises(ModuleDocumentSandboxError, match="安装 LibreOffice"):
        extract_module_document_isolated(
            source_path,
            source_path.name,
            title="缺少转换器",
            expected_hash=hashlib.sha256(data).hexdigest(),
            policy=DocumentParsePolicy(
                parser="builtin",
                # Process-spawn latency varies substantially on loaded desktop CI.
                # The converter's own one-second budget still tests the boundary.
                timeout_seconds=15,
                legacy_doc_converter_command=str(tmp_path / "missing-soffice"),
                legacy_doc_converter_timeout_seconds=1,
            ),
        )


@pytest.mark.skipif(os.name == "nt", reason="executable fixture uses a POSIX shebang")
def test_legacy_doc_import_reports_converter_failure_and_invalid_output(
    tmp_path: Path,
) -> None:
    data = _legacy_doc()
    source_path = tmp_path / "failed-conversion.doc"
    source_path.write_bytes(data)
    failing_converter = _fake_converter(
        tmp_path,
        """import sys
sys.stderr.write("writer filter unavailable")
raise SystemExit(7)
""",
    )

    with pytest.raises(ModuleDocumentSandboxError, match="退出代码 7"):
        extract_module_document_isolated(
            source_path,
            source_path.name,
            title="转换失败",
            expected_hash=hashlib.sha256(data).hexdigest(),
            policy=DocumentParsePolicy(
                parser="builtin",
                timeout_seconds=15,
                legacy_doc_converter_command=str(failing_converter),
                legacy_doc_converter_timeout_seconds=2,
            ),
        )

    invalid_converter = _fake_converter(
        tmp_path,
        """import sys
from pathlib import Path
args = sys.argv[1:]
outdir = Path(args[args.index("--outdir") + 1])
(outdir / "input.docx").write_bytes(b"not-a-docx")
""",
    )
    with pytest.raises(ModuleDocumentSandboxError, match="不是有效的 DOCX ZIP"):
        extract_module_document_isolated(
            source_path,
            source_path.name,
            title="无效产物",
            expected_hash=hashlib.sha256(data).hexdigest(),
            policy=DocumentParsePolicy(
                parser="builtin",
                timeout_seconds=15,
                legacy_doc_converter_command=str(invalid_converter),
                legacy_doc_converter_timeout_seconds=2,
            ),
        )


@pytest.mark.skipif(os.name == "nt", reason="executable fixture uses a POSIX shebang")
def test_legacy_doc_converter_timeout_kills_process_group(tmp_path: Path) -> None:
    data = _legacy_doc()
    source_path = tmp_path / "slow.doc"
    source_path.write_bytes(data)
    escaped_marker = tmp_path / "converter-escaped-timeout"
    slow_converter = _fake_converter(
        tmp_path,
        f"""import time
from pathlib import Path
time.sleep(1)
Path({str(escaped_marker)!r}).write_text("escaped", encoding="utf-8")
""",
    )

    with pytest.raises(ModuleDocumentSandboxError, match="转换超过"):
        extract_module_document_isolated(
            source_path,
            source_path.name,
            title="转换超时",
            expected_hash=hashlib.sha256(data).hexdigest(),
            policy=DocumentParsePolicy(
                parser="builtin",
                timeout_seconds=15,
                legacy_doc_converter_command=str(slow_converter),
                legacy_doc_converter_timeout_seconds=0.05,
            ),
        )
    time.sleep(1.1)
    assert not escaped_marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="executable fixture uses a POSIX shebang")
def test_legacy_doc_converter_enforces_diagnostic_and_output_limits(
    tmp_path: Path,
) -> None:
    data = _legacy_doc()
    source_path = tmp_path / "bounded.doc"
    source_path.write_bytes(data)
    noisy_converter = _fake_converter(
        tmp_path,
        """import sys
sys.stderr.write("x" * (70 * 1024))
""",
    )
    with pytest.raises(ModuleDocumentSandboxError, match="输出超过 64 KiB"):
        extract_module_document_isolated(
            source_path,
            source_path.name,
            title="输出上限",
            expected_hash=hashlib.sha256(data).hexdigest(),
            policy=DocumentParsePolicy(
                parser="builtin",
                timeout_seconds=15,
                legacy_doc_converter_command=str(noisy_converter),
                legacy_doc_converter_timeout_seconds=1,
            ),
        )

    oversized_converter = _fake_converter(
        tmp_path,
        f"""import sys
from pathlib import Path
args = sys.argv[1:]
outdir = Path(args[args.index("--outdir") + 1])
with (outdir / "input.docx").open("wb") as output:
    output.truncate({MAX_MODULE_BYTES + 1})
""",
    )
    with pytest.raises(ModuleDocumentSandboxError, match="超过 64 MiB"):
        extract_module_document_isolated(
            source_path,
            source_path.name,
            title="转换产物上限",
            expected_hash=hashlib.sha256(data).hexdigest(),
            policy=DocumentParsePolicy(
                parser="builtin",
                timeout_seconds=15,
                legacy_doc_converter_command=str(oversized_converter),
                legacy_doc_converter_timeout_seconds=1,
            ),
        )


@pytest.mark.skipif(os.name == "nt", reason="executable fixture uses a POSIX shebang")
def test_legacy_doc_real_import_preserves_original_hash_and_safe_arguments(
    tmp_path: Path,
) -> None:
    argument_log = tmp_path / "converter-arguments.txt"
    converter = _successful_fake_converter(tmp_path, argument_log=argument_log)
    data = _legacy_doc()
    settings = Settings(
        db_path=tmp_path / "legacy-doc.sqlite3",
        module_document_parser="builtin",
        module_asset_root=tmp_path / "module-assets",
        legacy_doc_converter_command=str(converter),
        legacy_doc_converter_timeout_seconds=2,
        local_admin_enabled=False,
        admin_token="module-admin",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        admin_headers = {"X-AI-KP-Admin-Token": "module-admin"}
        campaign = client.post(
            "/campaigns",
            headers=admin_headers,
            json={"title": "旧式 Word 团"},
        ).json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            headers=admin_headers,
            json={"kp_display_name": "OldOnes"},
        ).json()
        kp_headers = _bearer(session["access_token"])
        uploaded = client.post(
            f"/campaigns/{campaign['id']}/module-imports",
            params={"title": "常暗之厢"},
            headers={**kp_headers, "X-File-Name": "legacy-scenario.doc"},
            content=data,
        )

        assert uploaded.status_code == 202
        assert uploaded.json()["source_type"] == "docx"
        assert uploaded.json()["source_hash"] == hashlib.sha256(data).hexdigest()
        assert uploaded.json()["parser_version"] == PARSER_VERSION
        completed = _wait_for_import(client, uploaded.json()["id"], kp_headers)
        assert completed["status"] == "completed"
        chunks = client.get(
            f"/modules/{completed['module_id']}/chunks",
            headers=kp_headers,
        )
        assert chunks.status_code == 200
        assert chunks.json()[1]["source_locator"] == "docx:paragraph:2"
        assert chunks.json()[1]["heading_level"] is None
        assert chunks.json()[1]["section_path"] == ["第一章 雾港"]

    arguments = argument_log.read_text(encoding="utf-8").splitlines()
    assert "--headless" in arguments
    assert "--norestore" in arguments
    assert "--convert-to" in arguments
    assert "docx:Office Open XML Text" in arguments
    assert any(value.startswith("-env:UserInstallation=file://") for value in arguments)
    assert list(argument_log.parent.glob(".document-parse-*")) == []


def test_scenario_structure_hints_are_deterministic_and_require_review() -> None:
    assert infer_semantic_kind("<6号车厢>").semantic_kind == "heading"
    assert infer_semantic_kind("目击怪物进行 SAN 1/1D6 检定").semantic_kind == "san_check"
    clue = infer_semantic_kind("调查员可以发现座位下的旧车票线索。")
    assert clue.semantic_kind == "clue"
    assert clue.review_flags == ("verify_clue_role",)
    role, confidence, flags = infer_asset_role(
        filename="town-map.png",
        nearby_heading="小镇地图",
        width=1200,
        height=900,
    )
    assert (role, confidence, flags) == ("map", 0.92, ())


def test_image_only_pdf_is_imported_for_future_ocr() -> None:
    result = extract_module_document(_scanned_pdf(), "扫描线索.pdf", title="扫描线索")

    assert result.source_type == "pdf"
    assert result.unit_count == 1
    assert result.assets
    assert not result.chunks


def test_recoverable_non_strict_pdf_is_accepted() -> None:
    result = extract_module_document(
        _recoverable_non_strict_pdf(),
        "可修复.pdf",
        title="可修复 PDF",
    )

    assert result.source_type == "pdf"
    assert result.unit_count == 1


def test_compressed_pdf_stream_fails_without_harming_api_or_retry_semantics(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "compressed.sqlite3",
        module_document_parser="builtin",
        module_asset_root=tmp_path / "module-assets",
        local_admin_enabled=False,
        admin_token="module-admin",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        admin_headers = {"X-AI-KP-Admin-Token": "module-admin"}
        campaign = client.post(
            "/campaigns",
            headers=admin_headers,
            json={"title": "压缩流测试"},
        ).json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            headers=admin_headers,
            json={"kp_display_name": "OldOnes"},
        ).json()
        kp_headers = _bearer(session["access_token"])
        uploaded = client.post(
            f"/campaigns/{campaign['id']}/module-imports",
            params={"title": "危险压缩流"},
            headers={**kp_headers, "X-File-Name": "compressed.pdf"},
            content=_compressed_content_stream_pdf(),
        )

        assert uploaded.status_code == 202
        failed = _wait_for_import(
            client,
            uploaded.json()["id"],
            kp_headers,
            timeout=8,
        )
        assert failed["status"] == "failed"
        assert "内容流超过 16 MiB" in failed["error_text"]
        assert client.get("/health").status_code == 200

        retried = client.post(
            f"/module-imports/{failed['id']}/retry",
            headers=kp_headers,
        )
        assert retried.status_code == 202
        failed_again = _wait_for_import(
            client,
            failed["id"],
            kp_headers,
            timeout=8,
        )
        assert failed_again["status"] == "failed"
        assert failed_again["attempt_count"] == 2
        assert client.get("/health").status_code == 200


def test_isolated_parser_enforces_wall_timeout_and_cleans_temporary_output(
    tmp_path: Path,
) -> None:
    data = _docx()
    source_path = tmp_path / "timeout.docx"
    source_path.write_bytes(data)

    try:
        extract_module_document_isolated(
            source_path,
            source_path.name,
            title="超时测试",
            expected_hash=hashlib.sha256(data).hexdigest(),
            policy=DocumentParsePolicy(timeout_seconds=0.000001),
        )
    except ModuleDocumentSandboxError as exc:
        assert "解析超过" in str(exc)
    else:
        raise AssertionError("The isolated parser unexpectedly escaped its deadline")

    assert list(tmp_path.glob(".document-parse-*")) == []


def test_kp_document_import_realcase_is_durable_retryable_and_private(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "ai-kp.sqlite3",
        module_document_parser="builtin",
        module_asset_root=tmp_path / "module-assets",
        llm_base_url="",
        llm_model="",
        local_admin_enabled=False,
        admin_token="module-admin",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        admin_headers = {"X-AI-KP-Admin-Token": "module-admin"}
        campaign = client.post(
            "/campaigns",
            headers=admin_headers,
            json={"title": "雾港 1928"},
        ).json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            headers=admin_headers,
            json={"kp_display_name": "OldOnes"},
        ).json()
        kp_headers = _bearer(session["access_token"])
        player = client.post(
            "/sessions/join",
            json={"join_code": session["join_code"], "display_name": "调查员"},
        ).json()
        player_headers = _bearer(player["access_token"])

        uploaded = client.post(
            f"/campaigns/{campaign['id']}/module-imports",
            params={"title": "雾港疑云"},
            headers={
                **kp_headers,
                "X-File-Name": "mist-harbor.docx",
                "Content-Type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
            },
            content=_docx(),
        )
        assert uploaded.status_code == 202
        assert uploaded.json()["status"] == "queued"
        job = _wait_for_import(client, uploaded.json()["id"], kp_headers)
        assert job["status"] == "completed"
        assert job["module_id"]

        chunks = client.get(
            f"/modules/{job['module_id']}/chunks",
            headers=kp_headers,
        )
        assets = client.get(
            f"/modules/{job['module_id']}/assets",
            headers=kp_headers,
        )
        assert chunks.status_code == 200
        assert chunks.json()[1]["source_locator"] == "docx:paragraph:2"
        assert assets.status_code == 200
        assert assets.json()[0]["analysis_status"] == "pending_analysis"
        capabilities = client.get(
            "/module-analysis/capabilities",
            params={"campaign_id": campaign["id"]},
            headers=kp_headers,
        )
        assert capabilities.status_code == 200
        assert "languages" in capabilities.json()["tesseract"]

        first_chapter = chunks.json()[1]
        scoped = client.patch(
            f"/modules/{job['module_id']}/sections",
            headers=kp_headers,
            json={
                "title": first_chapter["title"],
                "visibility": "player",
                "spoiler_tag": "act-2",
            },
        )
        assert scoped.status_code == 200
        assert scoped.json()["chunk_count"] >= 1
        hidden_search = client.get(
            f"/modules/{job['module_id']}/search",
            params={"q": "旧照片"},
            headers=player_headers,
        )
        assert hidden_search.status_code == 200
        assert hidden_search.json() == []
        forced_spoiler_search = client.get(
            f"/modules/{job['module_id']}/search",
            params={"q": "旧照片", "spoiler_tag": "act-2"},
            headers=player_headers,
        )
        assert forced_spoiler_search.status_code == 200
        assert forced_spoiler_search.json() == []

        candidate = client.post(
            f"/modules/{job['module_id']}/knowledge/candidates",
            headers=kp_headers,
            json={
                "kind": "module_anchor",
                "title": "仓库照片",
                "statement": "仓库中的旧照片揭示照片中的真相。",
                "confidence": 1,
                "visibility": "kp",
                "spoiler_tag": "act-2",
                "citations": [
                    {
                        "chunk_id": first_chapter["id"],
                        "evidence_text": "码头仓库中藏着一张旧照片。",
                    }
                ],
            },
        )
        assert candidate.status_code == 200
        assert candidate.json()["status"] == "pending"
        reviewed = client.post(
            f"/module-knowledge/{candidate.json()['id']}/review",
            headers=kp_headers,
            json={"decision": "approved"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["status"] == "approved"
        entity_payloads = (
            ("location", "仓库"),
            ("clue", "旧照片"),
            ("anchor", "照片中的真相"),
        )
        graph_entities = []
        for entity_type, name in entity_payloads:
            created_entity = client.post(
                f"/modules/{job['module_id']}/entities",
                headers=kp_headers,
                json={
                    "entity_type": entity_type,
                    "name": name,
                    "spoiler_tag": "act-2",
                    "source_candidate_id": candidate.json()["id"],
                },
            )
            assert created_entity.status_code == 200
            graph_entities.append(created_entity.json())
        for source, predicate, target in (
            (graph_entities[0], "leads_to", graph_entities[1]),
            (graph_entities[1], "reveals", graph_entities[2]),
        ):
            relation = client.post(
                f"/modules/{job['module_id']}/relations",
                headers=kp_headers,
                json={
                    "source_entity_id": source["id"],
                    "predicate": predicate,
                    "target_entity_id": target["id"],
                    "spoiler_tag": "act-2",
                    "source_candidate_id": candidate.json()["id"],
                },
            )
            assert relation.status_code == 200
        reachability = client.post(
            f"/modules/{job['module_id']}/graph/reachability",
            headers=kp_headers,
            json={"entry_entity_ids": [graph_entities[0]["id"]]},
        )
        assert reachability.status_code == 200
        assert reachability.json()["all_anchors_reachable"] is True
        assert reachability.json()["anchors"][0]["name"] == "照片中的真相"
        assert client.get(
            f"/modules/{job['module_id']}/entities",
            headers=player_headers,
        ).status_code == 403
        kp_search = client.get(
            f"/modules/{job['module_id']}/search",
            params={"q": "旧照片", "spoiler_tag": "act-2"},
            headers=kp_headers,
        )
        assert kp_search.status_code == 200
        assert {item["source_type"] for item in kp_search.json()} >= {
            "chunk",
            "knowledge",
        }
        content = client.get(
            f"/module-assets/{assets.json()[0]['id']}/content",
            headers=kp_headers,
        )
        assert content.status_code == 200
        assert content.content == _png()

        assert client.get(
            f"/modules/{job['module_id']}/assets",
            headers=player_headers,
        ).status_code == 403
        assert client.get(
            f"/module-assets/{assets.json()[0]['id']}/content",
            headers=player_headers,
        ).status_code == 403

        failed_upload = client.post(
            f"/campaigns/{campaign['id']}/module-imports",
            params={"title": "损坏的本"},
            headers={**kp_headers, "X-File-Name": "broken.docx"},
            content=_docx(valid_xml=False),
        )
        failed = _wait_for_import(client, failed_upload.json()["id"], kp_headers)
        assert failed["status"] == "failed"
        retried = client.post(
            f"/module-imports/{failed['id']}/retry",
            headers=kp_headers,
        )
        assert retried.status_code == 202
        failed_again = _wait_for_import(client, failed["id"], kp_headers)
        assert failed_again["status"] == "failed"
        assert failed_again["attempt_count"] == 2

    restarted = create_app(settings)
    with TestClient(restarted) as client:
        jobs = client.get(
            f"/campaigns/{campaign['id']}/module-imports",
            headers=kp_headers,
        )
        restored_assets = client.get(
            f"/modules/{job['module_id']}/assets",
            headers=kp_headers,
        )
        assert jobs.status_code == 200
        assert len(jobs.json()) == 2
        assert restored_assets.status_code == 200
        assert (
            settings.module_asset_root
            / restored_assets.json()[0]["storage_path"]
        ).is_file()


def test_module_worker_recovers_processing_job_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "recovery.sqlite3"
    asset_root = tmp_path / "module-assets"
    connection = connect(db_path)
    try:
        init_db(connection)
        campaign = Repository(connection).create_campaign("恢复测试")
        connection.commit()
    finally:
        connection.close()

    queued = enqueue_module_import(
        db_path,
        asset_root,
        campaign_id=campaign["id"],
        title="中断的模组",
        source_filename="interrupted.docx",
        data=_docx(),
    )
    interrupted_connection = connect(db_path)
    try:
        interrupted = Repository(interrupted_connection).claim_module_import_job(queued["id"])
        interrupted_connection.commit()
        assert interrupted is not None
        assert interrupted["status"] == "processing"
        assert interrupted["attempt_count"] == 1
    finally:
        interrupted_connection.close()

    worker = ModuleImportWorker(
        db_path,
        asset_root,
        poll_interval_seconds=0.01,
        parse_policy=DocumentParsePolicy(parser="builtin"),
    )
    worker.start()
    try:
        deadline = time.monotonic() + 5
        recovered: dict | None = None
        while time.monotonic() < deadline:
            observer = connect(db_path)
            try:
                recovered = Repository(observer).get_module_import_job(queued["id"])
            finally:
                observer.close()
            if recovered["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
    finally:
        worker.stop()

    assert worker.running is False
    assert recovered is not None
    assert recovered["status"] == "completed"
    assert recovered["attempt_count"] == 2
    assert recovered["module_id"]


def test_recovered_import_rejects_completion_from_stale_attempt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "stale-attempt.sqlite3"
    asset_root = tmp_path / "module-assets"
    connection = connect(db_path)
    try:
        init_db(connection)
        campaign = Repository(connection).create_campaign("领取代次测试")
        connection.commit()
    finally:
        connection.close()

    queued = enqueue_module_import(
        db_path,
        asset_root,
        campaign_id=campaign["id"],
        title="不会重复的模组",
        source_filename="attempt.docx",
        data=_docx(),
    )
    extracted = extract_module_document(
        _docx(),
        "attempt.docx",
        title="不会重复的模组",
    )

    stale_connection = connect(db_path)
    recovery_connection = connect(db_path)
    try:
        stale_repo = Repository(stale_connection)
        stale_job = stale_repo.claim_module_import_job(queued["id"])
        stale_connection.commit()
        assert stale_job is not None
        assert stale_job["attempt_count"] == 1

        recovery_repo = Repository(recovery_connection)
        assert recovery_repo.recover_interrupted_module_imports() == 1
        recovery_connection.commit()

        try:
            stale_repo.complete_module_document_import(
                queued["id"],
                expected_attempt=stale_job["attempt_count"],
                chunks=extracted.chunks,
                assets=(),
            )
        except ValueError as exc:
            assert "no longer current" in str(exc)
            stale_connection.rollback()
        else:
            raise AssertionError("A stale import attempt unexpectedly completed")

        assert (
            recovery_connection.execute(
                "SELECT COUNT(*) FROM modules WHERE campaign_id = ?",
                (campaign["id"],),
            ).fetchone()[0]
            == 0
        )

        fresh_job = recovery_repo.claim_module_import_job(queued["id"])
        recovery_connection.commit()
        assert fresh_job is not None
        assert fresh_job["attempt_count"] == 2

        try:
            stale_repo.update_module_import_progress(
                queued["id"],
                expected_attempt=stale_job["attempt_count"],
                stage="storing",
            )
        except ValueError:
            stale_connection.rollback()
        else:
            raise AssertionError("A stale import attempt updated fresh progress")
        stale_failure = stale_repo.fail_module_import_job(
            queued["id"],
            "stale worker failure",
            expected_attempt=stale_job["attempt_count"],
        )
        stale_connection.commit()
        assert stale_failure["status"] == "processing"
        assert stale_failure["attempt_count"] == 2

        completed = recovery_repo.complete_module_document_import(
            queued["id"],
            expected_attempt=fresh_job["attempt_count"],
            chunks=extracted.chunks,
            assets=(),
        )
        recovery_connection.commit()

        assert completed["status"] == "completed"
        assert (
            recovery_connection.execute(
                "SELECT COUNT(*) FROM modules WHERE campaign_id = ?",
                (campaign["id"],),
            ).fetchone()[0]
            == 1
        )
    finally:
        stale_connection.close()
        recovery_connection.close()
