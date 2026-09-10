"""Regression tests for importing approved module entities into campaign travel
facts and NPC profiles (PRD 12.1 item 6)."""

from pathlib import Path

import pytest

from ai_kp.application.module_campaign_import_service import (
    ImportLocationChoice,
    ImportNpcChoice,
    ImportRouteChoice,
    ModuleCampaignImportService,
    ModuleImportConfirmCommand,
)
from ai_kp.application.module_graph_service import ModuleGraphService
from ai_kp.application.module_knowledge_service import ModuleKnowledgeService
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.modules.graph import ModuleEntityCreate, ModuleRelationCreate


def _seed_campaign(tmp_path: Path) -> tuple[Repository, str]:
    connection = connect(tmp_path / "import.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    campaign = repo.create_campaign("雾港")
    return repo, campaign["id"]


def _module_with_candidate(
    repo: Repository,
    campaign_id: str,
    *,
    candidate_text: str = (
        "雾港有灯塔、守塔人与邮局，旅店老板常在旅店接待来往商人。"
    ),
) -> tuple[str, str]:
    module_id = "mod_import"
    chunk_id = "chunk_import"
    repo.connection.execute(
        """
        INSERT INTO modules (id, campaign_id, title, source_type)
        VALUES (?, ?, '雾港疑云', 'docx')
        """,
        (module_id, campaign_id),
    )
    repo.connection.execute(
        """
        INSERT INTO module_chunks
          (id, module_id, title, text, visibility, order_index, source_locator)
        VALUES (?, ?, '第一章', ?, 'kp', 0, 'docx:paragraph:2')
        """,
        (chunk_id, module_id, candidate_text),
    )
    candidate = ModuleKnowledgeService(repo).create_manual_candidate(
        module_id,
        {
            "kind": "module_anchor",
            "title": "雾港概览",
            "statement": candidate_text,
            "visibility": "kp",
            "citations": [
                {
                    "chunk_id": chunk_id,
                    "evidence_text": candidate_text,
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
    return module_id, candidate["id"]


def _add_location(repo: Repository, module_id: str, name: str, candidate_id: str) -> dict:
    return ModuleGraphService(repo).create_entity(
        module_id,
        ModuleEntityCreate(
            entity_type="location",
            name=name,
            source_candidate_id=candidate_id,
        ),
        member_id=None,
    )


def _add_npc(repo: Repository, module_id: str, name: str, candidate_id: str) -> dict:
    return ModuleGraphService(repo).create_entity(
        module_id,
        ModuleEntityCreate(
            entity_type="npc",
            name=name,
            source_candidate_id=candidate_id,
        ),
        member_id=None,
    )


def _add_relation(
    repo: Repository,
    module_id: str,
    source: dict,
    target: dict,
    predicate: str,
    candidate_id: str,
) -> dict:
    return ModuleGraphService(repo).create_relation(
        module_id,
        ModuleRelationCreate(
            source_entity_id=source["id"],
            predicate=predicate,
            target_entity_id=target["id"],
            source_candidate_id=candidate_id,
        ),
        member_id=None,
    )


def _import_service(repo: Repository) -> ModuleCampaignImportService:
    return ModuleCampaignImportService(repo)


def test_preview_exposes_approved_locations_routes_and_npc_profiles(
    tmp_path: Path,
) -> None:
    repo, campaign_id = _seed_campaign(tmp_path)
    module_id, candidate_id = _module_with_candidate(repo, campaign_id)
    lighthouse = _add_location(repo, module_id, "灯塔", candidate_id)
    post_office = _add_location(repo, module_id, "邮局", candidate_id)
    innkeeper = _add_npc(repo, module_id, "旅店老板", candidate_id)
    _add_relation(repo, module_id, lighthouse, post_office, "located_at", candidate_id)
    _add_relation(repo, module_id, innkeeper, post_office, "located_at", candidate_id)
    repo.create_npc("旅店老板", home_location="雾港", profession="店主")
    repo.commit()

    preview = _import_service(repo).preview(campaign_id, module_id)
    assert preview["module_title"] == "雾港疑云"
    location_names = {item["name"] for item in preview["locations"]}
    assert "灯塔" in location_names and "邮局" in location_names
    route_froms = {item["from_name"] for item in preview["routes"]}
    assert "灯塔" in route_froms
    npc_names = {item["npc_name"] for item in preview["npc_profiles"]}
    assert "旅店老板" in npc_names


def test_confirm_creates_source_tracked_locations_routes_and_skips_unlinked_npc(
    tmp_path: Path,
) -> None:
    repo, campaign_id = _seed_campaign(tmp_path)
    module_id, candidate_id = _module_with_candidate(repo, campaign_id)
    lighthouse = _add_location(repo, module_id, "灯塔", candidate_id)
    post_office = _add_location(repo, module_id, "邮局", candidate_id)
    innkeeper = _add_npc(repo, module_id, "旅店老板", candidate_id)
    route = _add_relation(repo, module_id, lighthouse, post_office, "located_at", candidate_id)
    _add_relation(repo, module_id, innkeeper, post_office, "located_at", candidate_id)
    # The global NPC exists but is NOT linked to this campaign yet.
    repo.create_npc("旅店老板", home_location="雾港", profession="店主")
    repo.commit()

    result = _import_service(repo).confirm(
        campaign_id,
        module_id,
        ModuleImportConfirmCommand(
            locations=(
                ImportLocationChoice(lighthouse["id"]),
                ImportLocationChoice(post_office["id"]),
            ),
            routes=(ImportRouteChoice(route["id"]),),
            npcs=(ImportNpcChoice(innkeeper["id"]),),
        ),
        member_id=None,
    )
    assert len(result["created_locations"]) == 2
    assert len(result["created_routes"]) == 1
    assert result["skipped_npcs"] == ["旅店老板"]

    locations = repo.list_travel_locations(campaign_id)
    assert len(locations) == 2
    by_name = {item["name"]: item for item in locations}
    assert by_name["灯塔"]["source_kind"] == "module"
    assert by_name["灯塔"]["source_ref"] == lighthouse["id"]
    assert by_name["邮局"]["source_kind"] == "module"

    routes = repo.list_travel_routes(campaign_id)
    assert len(routes) == 1

    imports = repo.list_module_campaign_imports(campaign_id)
    assert len(imports) == 1
    assert len(imports[0]["items"]) == 3  # two locations + one route


def test_confirm_is_idempotent_for_locations_and_routes(tmp_path: Path) -> None:
    repo, campaign_id = _seed_campaign(tmp_path)
    module_id, candidate_id = _module_with_candidate(repo, campaign_id)
    lighthouse = _add_location(repo, module_id, "灯塔", candidate_id)
    post_office = _add_location(repo, module_id, "邮局", candidate_id)
    route = _add_relation(repo, module_id, lighthouse, post_office, "located_at", candidate_id)
    repo.commit()

    service = _import_service(repo)
    command = ModuleImportConfirmCommand(
        locations=(
            ImportLocationChoice(lighthouse["id"]),
            ImportLocationChoice(post_office["id"]),
        ),
        routes=(ImportRouteChoice(route["id"]),),
    )
    first = service.confirm(campaign_id, module_id, command, member_id=None)
    second = service.confirm(campaign_id, module_id, command, member_id=None)

    assert len(first["created_locations"]) == 2
    assert second["created_locations"] == []
    assert second["merged_locations"] == ["灯塔", "邮局"]
    assert second["created_routes"] == []
    assert len(repo.list_travel_locations(campaign_id)) == 2
    assert len(repo.list_travel_routes(campaign_id)) == 1


def test_confirm_rejects_duplicate_choices_before_writing(tmp_path: Path) -> None:
    repo, campaign_id = _seed_campaign(tmp_path)
    module_id, candidate_id = _module_with_candidate(repo, campaign_id)
    lighthouse = _add_location(repo, module_id, "灯塔", candidate_id)
    post_office = _add_location(repo, module_id, "邮局", candidate_id)
    innkeeper = _add_npc(repo, module_id, "旅店老板", candidate_id)
    route = _add_relation(
        repo,
        module_id,
        lighthouse,
        post_office,
        "located_at",
        candidate_id,
    )
    repo.commit()
    service = _import_service(repo)

    duplicate_commands = (
        ModuleImportConfirmCommand(
            locations=(
                ImportLocationChoice(lighthouse["id"]),
                ImportLocationChoice(lighthouse["id"], include=False),
            )
        ),
        ModuleImportConfirmCommand(
            routes=(
                ImportRouteChoice(route["id"]),
                ImportRouteChoice(route["id"], travel_minutes=15),
            )
        ),
        ModuleImportConfirmCommand(
            npcs=(
                ImportNpcChoice(innkeeper["id"]),
                ImportNpcChoice(innkeeper["id"], active_from_year=1920),
            )
        ),
    )
    for command in duplicate_commands:
        with pytest.raises(ValueError, match="不能重复选择"):
            service.confirm(campaign_id, module_id, command, member_id=None)

    assert repo.list_travel_locations(campaign_id) == []
    assert repo.list_module_campaign_imports(campaign_id) == []


def test_confirm_rejects_non_positive_route_minutes_at_service_boundary(
    tmp_path: Path,
) -> None:
    repo, campaign_id = _seed_campaign(tmp_path)
    module_id, candidate_id = _module_with_candidate(repo, campaign_id)
    lighthouse = _add_location(repo, module_id, "灯塔", candidate_id)
    post_office = _add_location(repo, module_id, "邮局", candidate_id)
    route = _add_relation(
        repo,
        module_id,
        lighthouse,
        post_office,
        "located_at",
        candidate_id,
    )
    repo.commit()

    with pytest.raises(ValueError, match="必须大于 0"):
        _import_service(repo).confirm(
            campaign_id,
            module_id,
            ModuleImportConfirmCommand(
                locations=(
                    ImportLocationChoice(lighthouse["id"]),
                    ImportLocationChoice(post_office["id"]),
                ),
                routes=(ImportRouteChoice(route["id"], travel_minutes=0),),
            ),
            member_id=None,
        )

    assert repo.list_travel_locations(campaign_id) == []


def test_unlinked_npc_never_auto_links_or_creates_campaign_npc(tmp_path: Path) -> None:
    repo, campaign_id = _seed_campaign(tmp_path)
    module_id, candidate_id = _module_with_candidate(repo, campaign_id)
    post_office = _add_location(repo, module_id, "邮局", candidate_id)
    stranger = _add_npc(repo, module_id, "守塔人", candidate_id)
    _add_relation(repo, module_id, stranger, post_office, "located_at", candidate_id)
    repo.create_npc("守塔人")
    repo.commit()

    result = _import_service(repo).confirm(
        campaign_id,
        module_id,
        ModuleImportConfirmCommand(
            locations=(ImportLocationChoice(post_office["id"]),),
            npcs=(ImportNpcChoice(stranger["id"]),),
        ),
        member_id=None,
    )
    assert result["skipped_npcs"] == ["守塔人"]
    # No campaign_npcs row may be auto-created.
    assert len(repo.list_campaign_npc_ids(campaign_id)) == 0


def test_linked_npc_profile_gains_location_tags(tmp_path: Path) -> None:
    repo, campaign_id = _seed_campaign(tmp_path)
    module_id, candidate_id = _module_with_candidate(repo, campaign_id)
    post_office = _add_location(repo, module_id, "邮局", candidate_id)
    innkeeper = _add_npc(repo, module_id, "旅店老板", candidate_id)
    _add_relation(repo, module_id, innkeeper, post_office, "located_at", candidate_id)
    npc = repo.create_npc("旅店老板")
    repo.link_npc_to_campaign(campaign_id, npc["id"])
    repo.commit()

    result = _import_service(repo).confirm(
        campaign_id,
        module_id,
        ModuleImportConfirmCommand(
            locations=(ImportLocationChoice(post_office["id"]),),
            npcs=(ImportNpcChoice(innkeeper["id"]),),
        ),
        member_id=None,
    )
    assert result["updated_npc_profiles"] == [npc["id"]]
    assert result["skipped_npcs"] == []
    profile = repo.get_npc_availability_profile(npc["id"])
    assert "邮局" in profile["location_tags"]


def test_rejected_source_yields_no_importable_locations(tmp_path: Path) -> None:
    repo, campaign_id = _seed_campaign(tmp_path)
    module_id = "mod_rejected"
    chunk_id = "chunk_rejected"
    candidate_text = "雾港灯塔与邮局。"
    repo.connection.execute(
        """
        INSERT INTO modules (id, campaign_id, title, source_type)
        VALUES (?, ?, '雾港疑云', 'docx')
        """,
        (module_id, campaign_id),
    )
    repo.connection.execute(
        """
        INSERT INTO module_chunks
          (id, module_id, title, text, visibility, order_index, source_locator)
        VALUES (?, ?, '第一章', ?, 'kp', 0, 'docx:paragraph:2')
        """,
        (chunk_id, module_id, candidate_text),
    )
    candidate = ModuleKnowledgeService(repo).create_manual_candidate(
        module_id,
        {
            "kind": "module_anchor",
            "title": "雾港概览",
            "statement": candidate_text,
            "visibility": "kp",
            "citations": [
                {
                    "chunk_id": chunk_id,
                    "evidence_text": candidate_text,
                }
            ],
        },
    )
    repo.review_module_knowledge_candidate(
        candidate["id"],
        decision="rejected",
        member_id=None,
        note="证据不足",
    )
    # An entity can only be created from an approved source, so reject the
    # candidate first; the import preview must then surface nothing.
    repo.commit()

    preview = _import_service(repo).preview(campaign_id, module_id)
    assert preview["locations"] == []
    assert preview["npc_profiles"] == []
