"""Tests for post-import automatic knowledge extraction and entity confirmation."""

from pathlib import Path

from ai_kp.application.module_entity_materialization_service import (
    ModuleEntityMaterializationService,
    entity_type_for_candidate,
)
from ai_kp.bootstrap.settings import Settings
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.infrastructure.modules.auto_extract import auto_extract_and_confirm_entities
from ai_kp.platform.modules.ingestion import chunk_plaintext_module


def _world(db_path: Path):
    context = db_session(db_path)
    connection = context.__enter__()
    repo = Repository(connection)
    campaign = repo.create_campaign("自动抽取测试")
    module = repo.create_module(
        campaign["id"],
        "末班电车",
        chunk_plaintext_module(
            """# 4号车厢
@visibility=kp
在这边发现了休克的乘务员，如果使用《急救》成功的话可以让乘务员意识苏醒过来。
乘务员保管着驾驶室钥匙。
乘务员会被怪物吞噬。
""",
            title="末班电车",
        ),
    )
    return context, connection, repo, campaign, module


def _store_candidates(repo, module_id: str, specs: list[dict]) -> list[dict]:
    from ai_kp.application.module_knowledge_service import (
        ModuleKnowledgeService,
    )

    chunks = repo.list_module_chunks(module_id)
    assert chunks, "module has no chunks"
    chunk_id = str(chunks[0]["id"])
    service = ModuleKnowledgeService(repo)
    stored = []
    for spec in specs:
        statement = spec["statement"]
        payload = {
            "kind": spec.get("kind", "module_canon"),
            "title": spec["title"],
            "statement": statement,
            "rationale": spec.get("rationale", "原文明确给出。"),
            "confidence": spec.get("confidence", 0.9),
            "visibility": spec.get("visibility", "kp"),
            "spoiler_tag": spec.get("spoiler_tag"),
            "entity_name": spec.get("entity_name"),
            "entity_type": spec.get("entity_type"),
            "citations": [
                {
                    "chunk_id": chunk_id,
                    "asset_id": None,
                    "evidence_text": spec.get("evidence_text") or statement[:60],
                }
            ],
        }
        stored.append(service.create_manual_candidate(module_id, payload))
    return stored


def _approve(repo, candidates: list[dict]) -> None:
    from ai_kp.application.module_knowledge_service import (
        ModuleKnowledgeService,
    )

    service = ModuleKnowledgeService(repo)
    for candidate in candidates:
        service.review_candidate(
            str(candidate["id"]),
            decision="approved",
            member_id=None,
            note="test auto-confirm",
        )


def test_entity_attribution_groups_into_one_entity(tmp_path: Path) -> None:
    context, _, repo, _, module = _world(tmp_path / "auto-extract.sqlite3")
    try:
        candidates = _store_candidates(
            repo,
            module["id"],
            [
                {
                    "title": "乘务员休克",
                    "statement": "在这边发现了休克的乘务员。",
                    "entity_name": "乘务员",
                    "entity_type": "npc",
                    "evidence_text": "在这边发现了休克的乘务员",
                },
                {
                    "title": "乘务员保管钥匙",
                    "statement": "乘务员保管着驾驶室钥匙。",
                    "entity_name": "乘务员",
                    "entity_type": "npc",
                    "evidence_text": "乘务员保管着驾驶室钥匙",
                },
                {
                    "title": "乘务员被吞噬",
                    "statement": "乘务员会被怪物吞噬。",
                    "entity_name": "乘务员",
                    "entity_type": "npc",
                    "evidence_text": "乘务员会被怪物吞噬",
                },
            ],
        )
        _approve(repo, candidates)

        ModuleEntityMaterializationService(repo).materialize_approved(
            module["id"], candidates, member_id=None
        )

        entities = repo.list_module_entities(module["id"])
        assert len(entities) == 1, entities
        entity = entities[0]
        assert entity["name"] == "乘务员"
        assert entity["entity_type"] == "npc"
        # The entity row keeps only its primary statement. Additional statements
        # remain source-scoped and are filtered individually during retrieval.
        descriptions = {
            "在这边发现了休克的乘务员。",
            "乘务员保管着驾驶室钥匙。",
            "乘务员会被怪物吞噬。",
        }
        assert entity["description"] in descriptions
        links = repo.list_entity_candidates(entity["id"])
        assert len(links) == 3
        assert {item["statement"] for item in links} == {
            "在这边发现了休克的乘务员。",
            "乘务员保管着驾驶室钥匙。",
            "乘务员会被怪物吞噬。",
        }
    finally:
        context.__exit__(None, None, None)


def test_duplicate_group_attaches_sources_to_existing_entity(tmp_path: Path) -> None:
    context, _, repo, _, module = _world(tmp_path / "auto-extract-dup.sqlite3")
    try:
        candidates = _store_candidates(
            repo,
            module["id"],
            [
                {
                    "title": "乘务员休克",
                    "statement": "在这边发现了休克的乘务员。",
                    "entity_name": "乘务员",
                    "entity_type": "npc",
                    "evidence_text": "在这边发现了休克的乘务员",
                },
                {
                    "title": "乘务员被吞噬",
                    "statement": "乘务员会被怪物吞噬。",
                    "entity_name": "乘务员",
                    "entity_type": "npc",
                    "evidence_text": "乘务员会被怪物吞噬",
                },
            ],
        )
        _approve(repo, candidates)
        from ai_kp.application.module_graph_service import ModuleGraphService
        from ai_kp.platform.modules.graph import ModuleEntityCreate

        graph = ModuleGraphService(repo)
        # A pre-existing entity with the same name+type occupies the UNIQUE slot.
        graph.create_entity(
            module["id"],
            ModuleEntityCreate(
                entity_type="npc",
                name="乘务员",
                description="手动创建的已有实体。",
                visibility="kp",
                spoiler_tag=None,
                source_candidate_id=str(candidates[0]["id"]),
            ),
            member_id=None,
        )
        repo.commit()

        ModuleEntityMaterializationService(repo).materialize_approved(
            module["id"], candidates, member_id=None
        )

        entities = repo.list_module_entities(module["id"])
        assert len(entities) == 1
        assert entities[0]["description"] == "手动创建的已有实体。"
        links = repo.list_entity_candidates(entities[0]["id"])
        assert {item["id"] for item in links} == {
            candidate["id"] for candidate in candidates
        }
        statuses = {
            str(c["id"]): c["status"]
            for c in repo.list_module_knowledge_candidates(
                module["id"], status="approved"
            )
        }
        assert len(statuses) == 2
        assert all(s == "approved" for s in statuses.values())
    finally:
        context.__exit__(None, None, None)


def test_candidate_without_structured_attribution_is_not_materialized(
    tmp_path: Path,
) -> None:
    context, _, repo, _, module = _world(
        tmp_path / "auto-extract-legacy.sqlite3"
    )
    try:
        candidates = _store_candidates(
            repo,
            module["id"],
            [
                {
                    "title": "乘务员",
                    "statement": "在这边发现了休克的乘务员。",
                    "evidence_text": "在这边发现了休克的乘务员",
                }
            ],
        )
        _approve(repo, candidates)
        ModuleEntityMaterializationService(repo).materialize_approved(
            module["id"], candidates, member_id=None
        )
        entities = repo.list_module_entities(module["id"])
        assert entities == []
    finally:
        context.__exit__(None, None, None)


def test_entity_type_requires_structured_attribution() -> None:
    assert entity_type_for_candidate({"entity_type": "npc"}) == "npc"
    assert entity_type_for_candidate({"entity_type": "location"}) == "location"
    assert entity_type_for_candidate({"entity_type": "clue"}) == "clue"
    assert entity_type_for_candidate({"title": "untyped free text"}) is None


def test_auto_extract_entry_point_is_swallowed_on_missing_llm(tmp_path: Path) -> None:
    """The best-effort entry point must never raise when the model is unreachable."""
    context, connection, _, _, module = _world(
        tmp_path / "auto-extract-entry.sqlite3"
    )
    try:
        settings = Settings(
            llm_base_url="http://localhost:1/v1",
            llm_api_key="local",
            llm_model="fake",
        )
        auto_extract_and_confirm_entities(
            connection,
            module["id"],
            settings=settings,
        )
    finally:
        context.__exit__(None, None, None)
