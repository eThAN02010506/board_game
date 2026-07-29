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


def test_module_graph_cannot_weaken_approved_source_scope(tmp_path: Path) -> None:
    repo, module_id, candidate_id = _approved_source(tmp_path)
    repo.connection.execute(
        """
        UPDATE module_knowledge_candidates
        SET visibility = 'secret', spoiler_tag = 'ending'
        WHERE id = ?
        """,
        (candidate_id,),
    )
    service = ModuleGraphService(repo)

    with pytest.raises(ValueError, match="visibility"):
        service.create_entity(
            module_id,
            ModuleEntityCreate(
                entity_type="anchor",
                name="航海日志",
                visibility="player",
                spoiler_tag="ending",
                source_candidate_id=candidate_id,
            ),
            member_id=None,
        )
    with pytest.raises(ValueError, match="spoiler tag"):
        service.create_entity(
            module_id,
            ModuleEntityCreate(
                entity_type="anchor",
                name="航海日志",
                visibility="secret",
                source_candidate_id=candidate_id,
            ),
            member_id=None,
        )
    protected = service.create_entity(
        module_id,
        ModuleEntityCreate(
            entity_type="anchor",
            name="航海日志",
            visibility="secret",
            spoiler_tag="ending",
            source_candidate_id=candidate_id,
        ),
        member_id=None,
    )
    assert protected["visibility"] == "secret"
    assert protected["spoiler_tag"] == "ending"
    repo.connection.close()


def test_section_scope_change_invalidates_graph_provenance_until_reauthored(
    tmp_path: Path,
) -> None:
    repo, module_id, candidate_id = _approved_source(tmp_path)
    service = ModuleGraphService(repo)
    warehouse = service.create_entity(
        module_id,
        ModuleEntityCreate(
            entity_type="location",
            name="仓库",
            source_candidate_id=candidate_id,
        ),
        member_id=None,
    )
    log = service.create_entity(
        module_id,
        ModuleEntityCreate(
            entity_type="anchor",
            name="航海日志",
            source_candidate_id=candidate_id,
        ),
        member_id=None,
    )
    relation = service.create_relation(
        module_id,
        ModuleRelationCreate(
            source_entity_id=warehouse["id"],
            predicate="leads_to",
            target_entity_id=log["id"],
            source_candidate_id=candidate_id,
        ),
        member_id=None,
    )
    assert len(repo.list_module_entities(module_id)) == 2
    assert repo.list_module_relations(module_id)[0]["id"] == relation["id"]

    changed = repo.update_module_section_scope(
        module_id,
        title="第一章",
        visibility="secret",
        spoiler_tag="ending",
    )

    assert changed["revoked_candidate_count"] == 1
    candidate = repo.get_module_knowledge_candidate(candidate_id)
    assert candidate["status"] == "pending"
    assert repo.list_module_entities(module_id) == []
    assert repo.list_module_relations(module_id) == []
    assert repo.connection.execute(
        "SELECT COUNT(*) FROM module_entities WHERE module_id = ?",
        (module_id,),
    ).fetchone()[0] == 2
    assert repo.connection.execute(
        "SELECT COUNT(*) FROM module_entity_relations WHERE module_id = ?",
        (module_id,),
    ).fetchone()[0] == 1
    report = service.check_reachability(module_id, (warehouse["id"],))
    assert report["invalid_entry_entity_ids"] == [warehouse["id"]]
    assert report["reached_entity_ids"] == []
    assert report["anchors"] == []
    assert report["conflicts"] == []
    assert report["safe"] is False
    with pytest.raises(ValueError, match="approved"):
        service.create_relation(
            module_id,
            ModuleRelationCreate(
                source_entity_id=warehouse["id"],
                predicate="leads_to",
                target_entity_id=log["id"],
                source_candidate_id=candidate_id,
            ),
            member_id=None,
        )
    with pytest.raises(ValueError, match="visibility"):
        repo.review_module_knowledge_candidate(
            candidate_id,
            decision="approved",
            member_id=None,
            note=None,
        )
    assert repo.get_module_knowledge_candidate(candidate_id)["status"] == "pending"
    repo.connection.close()


def test_graph_reads_revalidate_current_citations_when_status_is_stale(
    tmp_path: Path,
) -> None:
    repo, module_id, candidate_id = _approved_source(tmp_path)
    service = ModuleGraphService(repo)
    warehouse = service.create_entity(
        module_id,
        ModuleEntityCreate(
            entity_type="location",
            name="仓库",
            source_candidate_id=candidate_id,
        ),
        member_id=None,
    )
    log = service.create_entity(
        module_id,
        ModuleEntityCreate(
            entity_type="anchor",
            name="航海日志",
            source_candidate_id=candidate_id,
        ),
        member_id=None,
    )
    service.create_relation(
        module_id,
        ModuleRelationCreate(
            source_entity_id=warehouse["id"],
            predicate="reveals",
            target_entity_id=log["id"],
            source_candidate_id=candidate_id,
        ),
        member_id=None,
    )

    repo.connection.execute(
        """
        UPDATE module_chunks
        SET visibility = 'secret', spoiler_tag = 'ending'
        WHERE module_id = ?
        """,
        (module_id,),
    )

    assert repo.get_module_knowledge_candidate(candidate_id)["status"] == "approved"
    with pytest.raises(ValueError, match="currently valid"):
        service.create_entity(
            module_id,
            ModuleEntityCreate(
                entity_type="npc",
                name="守塔人",
                source_candidate_id=candidate_id,
            ),
            member_id=None,
        )
    assert repo.list_module_entities(module_id) == []
    assert repo.list_module_relations(module_id) == []
    report = service.check_reachability(module_id, (warehouse["id"],))
    assert report["invalid_entry_entity_ids"] == [warehouse["id"]]
    assert report["reached_entity_ids"] == []
    assert report["anchors"] == []
    assert report["safe"] is False
    repo.connection.close()


def test_relation_can_connect_entities_revealed_in_different_acts(
    tmp_path: Path,
) -> None:
    repo, module_id, _candidate_id = _approved_source(tmp_path)
    service = ModuleGraphService(repo)
    chunk_id = repo.list_module_chunks(module_id)[0]["id"]

    def approved_candidate(kind: str, title: str, statement: str, tag: str) -> dict:
        candidate = ModuleKnowledgeService(repo).create_manual_candidate(
            module_id,
            {
                "kind": kind,
                "title": title,
                "statement": statement,
                "visibility": "kp",
                "spoiler_tag": tag,
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
        return repo.review_module_knowledge_candidate(
            candidate["id"],
            decision="approved",
            member_id=None,
            note=None,
        )

    act_one = approved_candidate(
        "module_canon",
        "仓库照片",
        "第一幕可以找到仓库照片。",
        "act-1",
    )
    act_two = approved_candidate(
        "module_anchor",
        "照片指向日志",
        "仓库照片指向航海日志。",
        "act-2",
    )
    photo = service.create_entity(
        module_id,
        ModuleEntityCreate(
            entity_type="clue",
            name="仓库照片",
            spoiler_tag="act-1",
            source_candidate_id=act_one["id"],
        ),
        member_id=None,
    )
    log = service.create_entity(
        module_id,
        ModuleEntityCreate(
            entity_type="anchor",
            name="航海日志",
            spoiler_tag="act-2",
            source_candidate_id=act_two["id"],
        ),
        member_id=None,
    )

    relation = service.create_relation(
        module_id,
        ModuleRelationCreate(
            source_entity_id=photo["id"],
            predicate="reveals",
            target_entity_id=log["id"],
            spoiler_tag="act-2",
            source_candidate_id=act_two["id"],
        ),
        member_id=None,
    )

    assert relation["spoiler_tag"] == "act-2"
    repo.connection.close()
