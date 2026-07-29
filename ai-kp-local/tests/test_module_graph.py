from pathlib import Path

import pytest

from ai_kp.application.module_graph_service import ModuleGraphService
from ai_kp.application.module_knowledge_service import ModuleKnowledgeService
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.modules.graph import ModuleEntityCreate, ModuleRelationCreate


def _approved_source(tmp_path: Path) -> tuple[Repository, str, str]:
    connection = connect(tmp_path / "graph.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    campaign = repo.create_campaign("雾港")
    module_id = "mod_graph"
    chunk_id = "chunk_graph"
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
        VALUES (?, ?, '第一章',
                '守塔人留下的仓库照片指向灯塔中的航海日志，失踪船员身份也必须查明。',
                'kp', 0,
                'docx:paragraph:2')
        """,
        (chunk_id, module_id),
    )
    candidate = ModuleKnowledgeService(repo).create_manual_candidate(
        module_id,
        {
            "kind": "module_anchor",
            "title": "航海日志",
            "statement": "仓库照片通向航海日志；失踪船员身份也必须保持可发现。",
            "visibility": "kp",
            "citations": [
                {
                    "chunk_id": chunk_id,
                    "evidence_text": (
                        "守塔人留下的仓库照片指向灯塔中的航海日志，"
                        "失踪船员身份也必须查明。"
                    ),
                }
            ],
        },
    )
    repo.review_module_knowledge_candidate(
        candidate["id"],
        decision="approved",
        member_id=None,
        note=None,
    )
    repo.commit()
    return repo, module_id, candidate["id"]


def test_module_graph_keeps_provenance_and_checks_anchor_reachability(
    tmp_path: Path,
) -> None:
    repo, module_id, candidate_id = _approved_source(tmp_path)
    service = ModuleGraphService(repo)

    def entity(entity_type: str, name: str) -> dict:
        return service.create_entity(
            module_id,
            ModuleEntityCreate(
                entity_type=entity_type,
                name=name,
                source_candidate_id=candidate_id,
            ),
            member_id=None,
        )

    warehouse = entity("location", "仓库")
    photo = entity("clue", "仓库照片")
    log = entity("anchor", "航海日志")
    other_anchor = entity("anchor", "失踪船员身份")

    def relation(source: dict, predicate: str, target: dict) -> dict:
        return service.create_relation(
            module_id,
            ModuleRelationCreate(
                source_entity_id=source["id"],
                predicate=predicate,
                target_entity_id=target["id"],
                source_candidate_id=candidate_id,
            ),
            member_id=None,
        )

    relation(warehouse, "leads_to", photo)
    relation(photo, "reveals", log)
    relation(warehouse, "blocks", log)
    report = service.check_reachability(module_id, (warehouse["id"],))

    anchors = {item["name"]: item["reachable"] for item in report["anchors"]}
    assert anchors == {"失踪船员身份": False, "航海日志": True}
    assert report["all_anchors_reachable"] is False
    assert report["conflicts"][0]["predicate"] == "blocks"
    assert report["reached_entity_ids"] == sorted(
        [warehouse["id"], photo["id"], log["id"]]
    )
    assert other_anchor["source_candidate_id"] == candidate_id
    repo.connection.close()


def test_module_graph_rejects_unapproved_sources_and_invalid_same_as(
    tmp_path: Path,
) -> None:
    repo, module_id, candidate_id = _approved_source(tmp_path)
    service = ModuleGraphService(repo)
    repo.connection.execute(
        "UPDATE module_knowledge_candidates SET status = 'rejected' WHERE id = ?",
        (candidate_id,),
    )
    with pytest.raises(ValueError, match="approved"):
        service.create_entity(
            module_id,
            ModuleEntityCreate(
                entity_type="npc",
                name="守塔人",
                source_candidate_id=candidate_id,
            ),
            member_id=None,
        )
    repo.connection.execute(
        "UPDATE module_knowledge_candidates SET status = 'approved' WHERE id = ?",
        (candidate_id,),
    )
    with pytest.raises(ValueError, match="not present"):
        service.create_entity(
            module_id,
            ModuleEntityCreate(
                entity_type="location",
                name="警察局",
                source_candidate_id=candidate_id,
            ),
            member_id=None,
        )
    npc = service.create_entity(
        module_id,
        ModuleEntityCreate(
            entity_type="npc",
            name="守塔人",
            source_candidate_id=candidate_id,
        ),
        member_id=None,
    )
    location = service.create_entity(
        module_id,
        ModuleEntityCreate(
            entity_type="location",
            name="灯塔",
            source_candidate_id=candidate_id,
        ),
        member_id=None,
    )
    with pytest.raises(ValueError, match="same type"):
        service.create_relation(
            module_id,
            ModuleRelationCreate(
                source_entity_id=npc["id"],
                predicate="same_as",
                target_entity_id=location["id"],
                source_candidate_id=candidate_id,
            ),
            member_id=None,
        )
    repo.connection.close()
