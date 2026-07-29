from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient
from PIL import Image

from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings
from ai_kp.platform.modules.documents import extract_module_document


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
  <w:p><w:r><w:t>码头仓库中藏着一张旧照片。</w:t></w:r>
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


def _scanned_pdf() -> bytes:
    output = BytesIO()
    Image.new("RGB", (120, 80), (220, 210, 190)).save(output, format="PDF")
    return output.getvalue()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_docx_extraction_preserves_heading_table_image_and_anchors() -> None:
    result = extract_module_document(_docx(), "雾港.docx", title="雾港疑云")

    assert result.source_type == "docx"
    assert [chunk.content_kind for chunk in result.chunks] == ["text", "text", "table"]
    assert result.chunks[1].title == "第一章 雾港"
    assert result.chunks[1].source_locator == "docx:paragraph:2"
    assert "人物 | 秘密" in result.chunks[2].text
    assert len(result.assets) == 1
    assert result.assets[0].source_locator == "docx:paragraph:2:image:1"
    assert result.assets[0].width == 40
    assert result.assets[0].height == 24


def test_image_only_pdf_is_imported_for_future_ocr() -> None:
    result = extract_module_document(_scanned_pdf(), "扫描线索.pdf", title="扫描线索")

    assert result.source_type == "pdf"
    assert result.unit_count == 1
    assert result.assets
    assert not result.chunks


def test_kp_document_import_realcase_is_durable_retryable_and_private(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "ai-kp.sqlite3",
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
        job = client.get(
            f"/module-imports/{uploaded.json()['id']}",
            headers=kp_headers,
        ).json()
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
            params={"q": "旧照片"},
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
        failed = client.get(
            f"/module-imports/{failed_upload.json()['id']}",
            headers=kp_headers,
        ).json()
        assert failed["status"] == "failed"
        retried = client.post(
            f"/module-imports/{failed['id']}/retry",
            headers=kp_headers,
        )
        assert retried.status_code == 202
        failed_again = client.get(
            f"/module-imports/{failed['id']}",
            headers=kp_headers,
        ).json()
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
