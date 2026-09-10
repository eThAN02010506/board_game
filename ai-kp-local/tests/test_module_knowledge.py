from __future__ import annotations

import asyncio
import json
import sqlite3
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


class _TransactionCheckingModuleLlm:
    def __init__(
        self,
        repo: Repository,
        db_path: Path,
        chunk_id: str,
        response: dict,
    ):
        self.repo = repo
        self.db_path = db_path
        self.chunk_id = chunk_id
        self.response = response

    async def complete(self, _messages, temperature: float = 0.7) -> str:
        if self.repo.connection.in_transaction:
            raise AssertionError("module LLM await started with an open transaction")
        observer = connect(self.db_path)
        try:
            chunk = observer.execute(
                "SELECT knowledge_status FROM module_chunks WHERE id = ?",
                (self.chunk_id,),
            ).fetchone()
            if chunk is None or chunk["knowledge_status"] != "processing":
                raise AssertionError("processing claim was not committed before await")
            observer.execute("BEGIN IMMEDIATE")
            observer.rollback()
        finally:
            observer.close()
        return json.dumps(self.response, ensure_ascii=False)


class _RecoveringModuleLlm:
    def __init__(
        self,
        db_path: Path,
        chunk_id: str,
        response: dict,
        *,
        fail_after_recovery: bool = False,
    ):
        self.db_path = db_path
        self.chunk_id = chunk_id
        self.response = response
        self.fail_after_recovery = fail_after_recovery

    async def complete(self, _messages, temperature: float = 0.7) -> str:
        recovery_connection = connect(self.db_path)
        try:
            recovery_repo = Repository(recovery_connection)
            current = recovery_repo.get_module_chunk(self.chunk_id)
            assert current["knowledge_status"] == "processing"
            assert current["attempt_count"] == 1
            assert recovery_repo.recover_interrupted_module_knowledge_extractions() == 1
            recovery_connection.commit()
            fresh_attempt = recovery_repo.claim_module_chunk_for_knowledge(
                self.chunk_id
            )
            recovery_connection.commit()
            assert fresh_attempt == 2
        finally:
            recovery_connection.close()
        if self.fail_after_recovery:
            raise RuntimeError("stale module extraction failed")
        return json.dumps(self.response, ensure_ascii=False)


class _StaticModuleLlm:
    def __init__(self, response: dict):
        self.response = response

    async def complete(self, _messages, temperature: float = 0.7) -> str:
        return json.dumps(self.response, ensure_ascii=False)


def _module_candidate_payload(chunk_id: str) -> dict:
    return {
        "candidates": [
            {
                "kind": "module_canon",
                "title": "仓库线索",
                "statement": "仓库中有一张旧照片。",
                "rationale": "来源正文直接陈述",
                "confidence": 0.95,
                "visibility": "kp",
                "spoiler_tag": None,
                "citations": [
                    {
                        "chunk_id": chunk_id,
                        "asset_id": None,
                        "evidence_text": "码头仓库中藏着一张旧照片。",
                    }
                ],
            }
        ]
    }


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


def test_candidate_cannot_weaken_source_visibility_or_spoiler_scope(
    tmp_path: Path,
) -> None:
    repo, module_id, chunk_id = _module_repo(tmp_path)
    repo.connection.execute(
        """
        UPDATE module_chunks
        SET visibility = 'secret', spoiler_tag = 'ending'
        WHERE id = ?
        """,
        (chunk_id,),
    )
    payload = {
        "kind": "module_canon",
        "title": "结局真相",
        "statement": "旧照片记录了结局真相。",
        "visibility": "player",
        "citations": [
            {
                "chunk_id": chunk_id,
                "evidence_text": "码头仓库中藏着一张旧照片。",
            }
        ],
    }

    with pytest.raises(ValueError, match="visibility"):
        ModuleKnowledgeService(repo).create_manual_candidate(
            module_id,
            payload,
        )
    with pytest.raises(ValueError, match="spoiler tag"):
        ModuleKnowledgeService(repo).create_manual_candidate(
            module_id,
            {**payload, "visibility": "secret"},
        )

    candidate = ModuleKnowledgeService(repo).create_manual_candidate(
        module_id,
        {
            **payload,
            "visibility": "secret",
            "spoiler_tag": "ending",
        },
    )
    assert candidate["visibility"] == "secret"
    assert candidate["spoiler_tag"] == "ending"
    repo.connection.close()


def test_module_llm_call_does_not_hold_sqlite_writer(tmp_path: Path) -> None:
    repo, module_id, chunk_id = _module_repo(tmp_path)
    llm = _TransactionCheckingModuleLlm(
        repo,
        tmp_path / "knowledge.sqlite3",
        chunk_id,
        {
            "candidates": [
                {
                    "kind": "module_canon",
                    "title": "仓库线索",
                    "statement": "仓库中有一张旧照片。",
                    "rationale": "来源正文直接陈述",
                    "confidence": 0.95,
                    "visibility": "kp",
                    "spoiler_tag": None,
                    "citations": [
                        {
                            "chunk_id": chunk_id,
                            "asset_id": None,
                            "evidence_text": "码头仓库中藏着一张旧照片。",
                        }
                    ],
                }
            ]
        },
    )

    result = asyncio.run(
        ModuleKnowledgeService(repo).extract(
            module_id,
            llm,
            model_name="transaction-check",
        )
    )

    assert result["accepted_count"] == 1
    assert len(result["accepted_candidate_ids"]) == 1
    assert repo.get_module_knowledge_candidate(
        result["accepted_candidate_ids"][0]
    )["title"] == "仓库线索"
    assert repo.get_module_chunk(chunk_id)["knowledge_status"] == "completed"
    repo.connection.close()


@pytest.mark.parametrize("failure_point", ("begin", "store"))
def test_unexpected_module_persistence_failure_releases_claim_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    repo, module_id, chunk_id = _module_repo(tmp_path)
    llm = _StaticModuleLlm(_module_candidate_payload(chunk_id))
    if failure_point == "begin":
        original = repo.begin_immediate
        failed_once = False

        def fail_once() -> None:
            nonlocal failed_once
            if not failed_once:
                failed_once = True
                raise sqlite3.OperationalError("injected begin failure")
            original()

        monkeypatch.setattr(repo, "begin_immediate", fail_once)
    else:
        original_store = repo.store_module_knowledge_candidate
        failed_once = False

        def fail_store_once(*args, **kwargs):
            nonlocal failed_once
            if not failed_once:
                failed_once = True
                raise sqlite3.OperationalError("injected candidate store failure")
            return original_store(*args, **kwargs)

        monkeypatch.setattr(
            repo,
            "store_module_knowledge_candidate",
            fail_store_once,
        )

    with pytest.raises(sqlite3.OperationalError, match="injected"):
        asyncio.run(
            ModuleKnowledgeService(repo).extract(
                module_id,
                llm,
                model_name="persistence-failure",
            )
        )

    failed = repo.get_module_chunk(chunk_id)
    assert failed["knowledge_status"] == "failed"
    assert failed["attempt_count"] == 1
    assert repo.list_module_knowledge_candidates(module_id) == []

    retried = asyncio.run(
        ModuleKnowledgeService(repo).extract(
            module_id,
            llm,
            model_name="persistence-retry",
            retry_failed=True,
        )
    )

    completed = repo.get_module_chunk(chunk_id)
    assert retried["accepted_count"] == 1
    assert completed["knowledge_status"] == "completed"
    assert completed["attempt_count"] == 2
    assert len(repo.list_module_knowledge_candidates(module_id)) == 1
    repo.connection.close()


def test_recovery_prevents_stale_module_results_and_completion(
    tmp_path: Path,
) -> None:
    repo, module_id, chunk_id = _module_repo(tmp_path)
    llm = _RecoveringModuleLlm(
        tmp_path / "knowledge.sqlite3",
        chunk_id,
        {
            "candidates": [
                {
                    "kind": "module_canon",
                    "title": "旧代次线索",
                    "statement": "旧代次不应保存。",
                    "rationale": "stale",
                    "confidence": 0.95,
                    "visibility": "kp",
                    "spoiler_tag": None,
                    "citations": [
                        {
                            "chunk_id": chunk_id,
                            "asset_id": None,
                            "evidence_text": "码头仓库中藏着一张旧照片。",
                        }
                    ],
                }
            ]
        },
    )

    result = asyncio.run(
        ModuleKnowledgeService(repo).extract(
            module_id,
            llm,
            model_name="stale-attempt",
        )
    )

    chunk = repo.get_module_chunk(chunk_id)
    assert result["processed_count"] == 0
    assert result["accepted_count"] == 0
    assert chunk["knowledge_status"] == "processing"
    assert chunk["attempt_count"] == 2
    assert repo.list_module_knowledge_candidates(module_id) == []
    assert (
        repo.mark_module_chunk_knowledge_status(
            chunk_id,
            "failed",
            expected_attempt=1,
        )
        is False
    )
    repo.commit()
    assert repo.get_module_chunk(chunk_id)["knowledge_status"] == "processing"
    repo.connection.close()


def test_recovery_prevents_stale_module_failure_from_overwriting_fresh_claim(
    tmp_path: Path,
) -> None:
    repo, module_id, chunk_id = _module_repo(tmp_path)
    llm = _RecoveringModuleLlm(
        tmp_path / "knowledge.sqlite3",
        chunk_id,
        {},
        fail_after_recovery=True,
    )

    result = asyncio.run(
        ModuleKnowledgeService(repo).extract(
            module_id,
            llm,
            model_name="stale-failure",
        )
    )

    chunk = repo.get_module_chunk(chunk_id)
    assert result["processed_count"] == 0
    assert result["rejected_count"] == 0
    assert chunk["knowledge_status"] == "processing"
    assert chunk["attempt_count"] == 2
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


def test_section_scope_change_revokes_chunk_and_asset_candidates_and_rechecks_review(
    tmp_path: Path,
) -> None:
    repo, module_id, chunk_id = _module_repo(tmp_path)
    repo.connection.execute(
        "UPDATE module_chunks SET visibility = 'player' WHERE id = ?",
        (chunk_id,),
    )
    asset_id = "asset_scope"
    repo.connection.execute(
        """
        INSERT INTO module_assets
          (id, module_id, content_hash, storage_path, mime_type, source_locator,
           nearby_heading, visibility, ocr_text)
        VALUES (?, ?, 'asset-scope-hash', 'scope/photo.png', 'image/png',
                'docx:paragraph:2:image:1', '第一章', 'player',
                '旧照片背面写着灯塔。')
        """,
        (asset_id, module_id),
    )
    service = ModuleKnowledgeService(repo)
    candidates = [
        service.create_manual_candidate(
            module_id,
            {
                "kind": "module_canon",
                "title": "仓库旧照片",
                "statement": "仓库中存在一张旧照片。",
                "visibility": "player",
                "citations": [
                    {
                        "chunk_id": chunk_id,
                        "evidence_text": "码头仓库中藏着一张旧照片。",
                    }
                ],
            },
        ),
        service.create_manual_candidate(
            module_id,
            {
                "kind": "module_anchor",
                "title": "照片背面的灯塔",
                "statement": "旧照片背面写着灯塔。",
                "visibility": "player",
                "citations": [
                    {
                        "asset_id": asset_id,
                        "evidence_text": "旧照片背面写着灯塔。",
                    }
                ],
            },
        ),
    ]
    for candidate in candidates:
        approved = service.review_candidate(
            candidate["id"],
            decision="approved",
            member_id=None,
            note="初次审核通过",
        )
        assert approved["status"] == "approved"
        assert approved["reviewed_at"] is not None

    changed = repo.update_module_section_scope(
        module_id,
        title="第一章",
        visibility="secret",
        spoiler_tag="ending",
    )

    assert changed["chunk_count"] == 1
    assert changed["asset_count"] == 1
    assert changed["revoked_candidate_count"] == 2
    for candidate in candidates:
        revoked = repo.get_module_knowledge_candidate(candidate["id"])
        assert revoked["status"] == "pending"
        assert revoked["review_note"] is None
        assert revoked["reviewed_by_member_id"] is None
        assert revoked["reviewed_at"] is None
        with pytest.raises(ValueError, match="visibility"):
            service.review_candidate(
                candidate["id"],
                decision="approved",
                member_id=None,
                note=None,
            )
        assert repo.get_module_knowledge_candidate(candidate["id"])["status"] == "pending"

    assert repo.search_module(
        module_id,
        "旧照片",
        allowed_visibility=("player", "table"),
        spoiler_tags=(),
    ) == []
    assert repo.search_module(
        module_id,
        "旧照片",
        allowed_visibility=("player", "table", "kp", "secret"),
        spoiler_tags=(),
    ) == []
    unchanged = repo.update_module_section_scope(
        module_id,
        title="第一章",
        visibility="secret",
        spoiler_tag="ending",
    )
    assert unchanged["revoked_candidate_count"] == 0
    repo.connection.close()


def test_search_revalidates_current_citation_scope_when_candidate_acl_is_stale(
    tmp_path: Path,
) -> None:
    repo, module_id, chunk_id = _module_repo(tmp_path)
    repo.connection.execute(
        "UPDATE module_chunks SET visibility = 'player' WHERE id = ?",
        (chunk_id,),
    )
    service = ModuleKnowledgeService(repo)
    candidate = service.create_manual_candidate(
        module_id,
        {
            "kind": "module_canon",
            "title": "公开旧照片",
            "statement": "仓库旧照片可以公开调查。",
            "visibility": "player",
            "citations": [
                {
                    "chunk_id": chunk_id,
                    "evidence_text": "码头仓库中藏着一张旧照片。",
                }
            ],
        },
    )
    service.review_candidate(
        candidate["id"],
        decision="approved",
        member_id=None,
        note=None,
    )
    assert any(
        item["source_id"] == candidate["id"]
        for item in repo.search_module(
            module_id,
            "旧照片",
            allowed_visibility=("player", "table"),
            spoiler_tags=(),
        )
    )

    # Simulate a legacy writer bypassing the section-scope use case. Read-time
    # authorization must still use the current source, not the indexed candidate ACL.
    repo.connection.execute(
        """
        UPDATE module_chunks
        SET visibility = 'secret', spoiler_tag = 'ending'
        WHERE id = ?
        """,
        (chunk_id,),
    )
    assert repo.get_module_knowledge_candidate(candidate["id"])["status"] == "approved"

    player_results = repo.search_module(
        module_id,
        "旧照片",
        allowed_visibility=("player", "table"),
        spoiler_tags=(),
    )
    assert player_results == []
    kp_ending_results = repo.search_module(
        module_id,
        "旧照片",
        allowed_visibility=("player", "table", "kp", "secret"),
        spoiler_tags=("ending",),
    )
    assert {item["source_type"] for item in kp_ending_results} == {"chunk"}
    assert all(item["source_id"] != candidate["id"] for item in kp_ending_results)
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


def test_openai_compatible_vision_streams_with_a_decoded_body_limit() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * (4 * 1024 * 1024 + 1),
            headers={"content-length": "invalid-on-purpose"},
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as client:
            analyzer = OpenAICompatibleVisionAnalyzer(
                "http://model.test/v1",
                "",
                "vision-local",
                client=client,
            )
            with pytest.raises(RuntimeError, match="4 MiB"):
                await analyzer.analyze(
                    b"\x89PNG\r\n",
                    mime_type="image/png",
                    source_locator="pdf:page:2:image:1",
                )

    asyncio.run(exercise())


def test_candidate_entity_attribution_round_trips(tmp_path: Path) -> None:
    repo, module_id, chunk_id = _module_repo(tmp_path)
    payload = {
        "kind": "module_canon",
        "title": "仓库线索",
        "statement": "码头仓库中藏着一张旧照片。",
        "confidence": 0.9,
        "visibility": "kp",
        "spoiler_tag": None,
        "entity_name": "仓库",
        "entity_type": "location",
        "citations": [
            {
                "chunk_id": chunk_id,
                "evidence_text": "码头仓库中藏着一张旧照片",
            }
        ],
    }
    candidate = ModuleKnowledgeService(repo).create_manual_candidate(
        module_id, payload
    )
    assert candidate["entity_name"] == "仓库"
    assert candidate["entity_type"] == "location"
    fetched = repo.get_module_knowledge_candidate(candidate["id"])
    assert fetched["entity_name"] == "仓库"
    assert fetched["entity_type"] == "location"
    repo.connection.close()


def test_candidate_entity_type_requires_entity_name(tmp_path: Path) -> None:
    repo, module_id, chunk_id = _module_repo(tmp_path)
    with pytest.raises(ValueError, match="entity_type requires entity_name"):
        ModuleKnowledgeService(repo).create_manual_candidate(
            module_id,
            {
                "kind": "module_canon",
                "title": "乘务员休克",
                "statement": "在这边发现了休克的乘务员。",
                "entity_type": "npc",
                "citations": [
                    {
                        "chunk_id": chunk_id,
                        "evidence_text": "在这边发现了休克的乘务员",
                    }
                ],
            },
        )
    repo.connection.close()
