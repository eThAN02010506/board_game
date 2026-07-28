from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from ai_kp.application.module_knowledge_service import ModuleKnowledgeService
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.infrastructure.modules.analysis import OpenAICompatibleVisionAnalyzer


def _module_repo(tmp_path: Path) -> tuple[Repository, str, str]:
    connection = connect(tmp_path / "knowledge.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    campaign = repo.create_campaign("雾港")
    module_id = "mod_test"
    chunk_id = "chunk_test"
    repo.connection.execute(
        """
        INSERT INTO modules (id, campaign_id, title, source_type)
        VALUES (?, ?, '雾港疑云', 'docx')
        """,
        (module_id, campaign["id"]),
    )
    repo.connection.execute(
        """
        INSERT INTO module_chunks
          (id, module_id, title, text, visibility, order_index, source_locator)
        VALUES (?, ?, '第一章', '码头仓库中藏着一张旧照片。', 'kp', 0,
                'docx:paragraph:2')
        """,
        (chunk_id, module_id),
    )
    repo.commit()
    return repo, module_id, chunk_id


def test_candidate_requires_exact_source_evidence_and_kp_review(tmp_path: Path) -> None:
    repo, module_id, chunk_id = _module_repo(tmp_path)
    payload = {
        "kind": "module_canon",
        "title": "仓库线索",
        "statement": "仓库中有一张旧照片。",
        "confidence": 0.9,
        "visibility": "kp",
        "citations": [
            {
                "chunk_id": chunk_id,
                "evidence_text": "码头仓库中藏着一张旧照片。",
            }
        ],
    }
    candidate = ModuleKnowledgeService(repo).create_manual_candidate(module_id, payload)
    assert candidate["status"] == "pending"
    assert candidate["citations"][0]["source_locator"] == "docx:paragraph:2"

    with pytest.raises(ValueError, match="not present"):
        ModuleKnowledgeService(repo).create_manual_candidate(
            module_id,
            {
                **payload,
                "title": "伪造线索",
                "citations": [
                    {
                        "chunk_id": chunk_id,
                        "evidence_text": "警长供认了罪行",
                    }
                ],
            },
        )

    reviewed = repo.review_module_knowledge_candidate(
        candidate["id"],
        decision="approved",
        member_id=None,
        note=None,
    )
    assert reviewed["status"] == "approved"
    results = repo.search_module(
        module_id,
        "旧照片",
        allowed_visibility=("kp",),
        spoiler_tags=None,
    )
    assert {item["source_type"] for item in results} == {"chunk", "knowledge"}
    repo.connection.close()


def test_search_enforces_visibility_and_spoiler_boundaries(tmp_path: Path) -> None:
    repo, module_id, _chunk_id = _module_repo(tmp_path)
    repo.connection.execute(
        """
        INSERT INTO module_chunks
          (id, module_id, title, text, visibility, spoiler_tag, order_index)
        VALUES
          ('chunk_public', ?, '公开', '钟楼线索', 'player', NULL, 1),
          ('chunk_spoiler', ?, '第二幕', '钟楼密室', 'table', 'act-2', 2),
          ('chunk_secret', ?, '结局', '钟楼真凶', 'secret', NULL, 3)
        """,
        (module_id, module_id, module_id),
    )
    repo.commit()

    player = repo.search_module(
        module_id,
        "钟楼",
        allowed_visibility=("player", "table"),
        spoiler_tags=(),
    )
    assert [item["source_id"] for item in player] == ["chunk_public"]
    unlocked = repo.search_module(
        module_id,
        "钟楼",
        allowed_visibility=("player", "table"),
        spoiler_tags=("act-2",),
    )
    assert {item["source_id"] for item in unlocked} == {
        "chunk_public",
        "chunk_spoiler",
    }
    repo.connection.close()


def test_openai_compatible_vision_uses_data_url_and_strict_json() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "ocr_text": "POLICE",
                                    "visual_summary": "一块警察局门牌",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    async def exercise() -> object:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await OpenAICompatibleVisionAnalyzer(
                "http://model.test/v1",
                "",
                "vision-local",
                client=client,
            ).analyze(
                b"\x89PNG\r\n",
                mime_type="image/png",
                source_locator="pdf:page:2:image:1",
            )

    result = asyncio.run(exercise())

    image_url = captured["messages"][1]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")
    assert result.ocr_text == "POLICE"
    assert result.visual_summary == "一块警察局门牌"
