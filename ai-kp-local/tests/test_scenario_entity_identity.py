import hashlib
import json
from pathlib import Path

import pytest

from ai_kp.application.errors import InvalidInputError
from ai_kp.application.module_graph_service import ModuleGraphService
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.application.scenario_entity_identity import validate_scenario_entity_identities
from ai_kp.application.world_service import WorldService
from ai_kp.platform.modules.graph import ModuleEntityCreate
from ai_kp.platform.resolution.contracts import EntitySpec, ScenarioContract
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from tests.scenario_contract_testkit import bind_payload_to_module
from tests.test_module_graph import _approved_source
from tests.test_scenario_contract_service import payload


def setup_identity(tmp_path: Path):
    repo, module_id, candidate_id = _approved_source(tmp_path)
    graph = ModuleGraphService(repo).create_entity(
        module_id,
        ModuleEntityCreate(entity_type="item", name="航海日志", source_candidate_id=candidate_id),
        member_id=None,
    )
    raw = payload()
    raw["entities"] = [
        {
            "entity_id": "ship-log",
            "entity_type": "item",
            "title": "航海日志",
            "module_entity_id": graph["id"],
        }
    ]
    return repo, module_id, candidate_id, graph, bind_payload_to_module(repo, module_id, raw)


def test_explicit_identity_survives_publication_and_resolves_materialized_entity(tmp_path: Path):
    repo, module_id, _, graph, raw = setup_identity(tmp_path)
    try:
        service = ScenarioContractService(repo)
        result, draft = service.compile_draft(module_id, raw, created_by_member_id=None)
        assert result.report.release_ready
        assert draft is not None
        published = service.publish(
            draft["id"], expected_row_version=1, published_by_member_id=None
        )
        campaign_id = repo.get_module(module_id)["campaign_id"]
        run = repo.start_campaign_module_run(
            campaign_id=campaign_id,
            module_id=module_id,
            current_scene_key=None,
            active_spoiler_tags=[],
            state={},
            started_by_member_id=None,
        )
        service.bind_run(run["id"], published["id"])
        event = repo.append_event(
            campaign_id=campaign_id, actor_type="system", event_type="encounter", summary="见到日志"
        )
        source = repo.create_campaign_world_entity(
            campaign_id=campaign_id,
            entity_kind="item",
            name="航海日志",
            visibility="table",
            origin_kind="module_source",
            origin_ref=graph["id"],
            created_from_event_id=event["id"],
        )
        same_name = repo.create_campaign_world_entity(
            campaign_id=campaign_id,
            entity_kind="item",
            name="航海日志",
            visibility="table",
            origin_kind="world_expansion",
            origin_ref="unrelated-log",
            created_from_event_id=event["id"],
        )
        projection = WorldService(repo).list_world_entities(campaign_id, view="kp")
        by_id = {item["id"]: item for item in projection["entities"]}
        assert by_id[source["id"]]["scenario_identity"]["entity_id"] == "ship-log"
        assert by_id[same_name["id"]]["scenario_identity"] is None
        public = WorldService(repo).list_world_entities(campaign_id, view="player")
        assert all("scenario_identity" not in item for item in public["entities"])
    finally:
        repo.connection.close()


def test_publication_and_run_binding_revalidate_withdrawn_identity(tmp_path: Path):
    repo, module_id, candidate_id, _, raw = setup_identity(tmp_path)
    try:
        service = ScenarioContractService(repo)
        _, draft = service.compile_draft(module_id, raw, created_by_member_id=None)
        assert draft is not None
        published = service.publish(
            draft["id"], expected_row_version=1, published_by_member_id=None
        )
        # Simulate a restored/repaired store whose old source is no longer approved.
        repo.connection.execute(
            "UPDATE module_knowledge_candidates SET status = 'rejected' WHERE id = ?",
            (candidate_id,),
        )
        campaign_id = repo.get_module(module_id)["campaign_id"]
        run = repo.start_campaign_module_run(
            campaign_id=campaign_id,
            module_id=module_id,
            current_scene_key=None,
            active_spoiler_tags=[],
            state={},
            started_by_member_id=None,
        )
        with pytest.raises(InvalidInputError, match="no longer confirmed"):
            service.bind_run(run["id"], published["id"])
        invalid, _ = service.compile_draft(module_id, raw, created_by_member_id=None)
        assert not invalid.report.release_ready
        assert any(item.code == "unknown_module_entity_identity" for item in invalid.report.issues)
    finally:
        repo.connection.close()


def test_missing_identity_preserves_legacy_contract_hash_and_same_name_does_not_bind():
    raw = payload()
    raw["entities"] = [{"entity_id": "log", "entity_type": "item", "title": "Same name"}]
    contract = ScenarioContract.model_validate(raw)
    old_payload = contract.model_dump(mode="json")
    assert "module_entity_id" not in old_payload["entities"][0]
    expected = hashlib.sha256(
        json.dumps(old_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert ScenarioContractCompiler.contract_hash(contract) == expected
    assert (
        validate_scenario_entity_identities(contract, [{"id": "different", "name": "Same name"}])
        == ()
    )
    unknown = contract.model_copy(
        update={
            "entities": (
                EntitySpec(
                    entity_id="a", entity_type="item", title="Same name", module_entity_id="missing"
                ),
            )
        }
    )
    assert (
        validate_scenario_entity_identities(unknown, [{"id": "different", "name": "Same name"}])[
            0
        ].code
        == "unknown_module_entity_identity"
    )
    duplicates = contract.model_copy(
        update={
            "entities": (
                EntitySpec(entity_id="a", entity_type="item", title="A", module_entity_id="same"),
                EntitySpec(entity_id="b", entity_type="item", title="B", module_entity_id="same"),
            )
        }
    )
    assert (
        validate_scenario_entity_identities(duplicates, [{"id": "same"}])[0].code
        == "duplicate_module_entity_identity"
    )
