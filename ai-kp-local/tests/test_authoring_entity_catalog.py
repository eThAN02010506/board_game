import json

import pytest

from ai_kp.application.errors import ConflictError
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.infrastructure.scenario_contract_worker import _assert_job_source_unchanged
from ai_kp.platform.resolution.authoring_entity_catalog import validate_authoring_entities
from ai_kp.platform.resolution.contracts import WorldCommand
from ai_kp.platform.resolution.scenario_authoring import ConstrainedScenarioContractAuthoringAdapter
from ai_kp.platform.resolution.scenario_entity_identity import converge_scenario_entity_identities
from ai_kp.platform.resolution.scenario_ir import ScenarioIrAssembler
from ai_kp.platform.resolution.scenario_ir_models import IrEntity
from tests.test_scenario_ir import batch, source_ref
from tests.test_scenario_world_effects import setup_world_effect


def test_preparation_snapshots_cited_identities_dimensions_and_detects_catalog_drift(tmp_path):
    repo, _, run, _, _, entity = setup_world_effect(tmp_path)
    try:
        service = ScenarioContractService(repo)
        prepared = service.prepare_generation(run["module_id"])
        candidates = [
            candidate for item in prepared.evidence for candidate in item.entity_candidates
        ]
        assert len(candidates) == 1
        assert candidates[0].module_entity_id == entity["origin_ref"]
        assert candidates[0].state_dimensions == ("condition",)
        assert candidates[0].state_visibility_floor == "kp"
        assert "value" not in candidates[0].model_dump()
        messages = ConstrainedScenarioContractAuthoringAdapter._messages(
            prepared.evidence, ruleset_id="coc7", partition_index=0, errors=[], effect_catalog=None
        )
        assert entity["origin_ref"] in messages[1].content
        assert "set_world_entity_state" in messages[0].content
        job = {"module_id": run["module_id"], "source_fingerprint": prepared.source_fingerprint}
        _assert_job_source_unchanged(service, job, prepared.evidence)
        repo.connection.execute(
            "UPDATE campaign_world_entities SET data_json=? WHERE id=?",
            (json.dumps({"state_dimensions": ["different"]}), entity["id"]),
        )
        with pytest.raises(ConflictError, match="source changed"):
            _assert_job_source_unchanged(service, job, prepared.evidence)
    finally:
        repo.connection.close()


def test_authoring_identity_and_dimension_validation_uses_exact_cited_catalog(tmp_path):
    repo, _, run, _, _, _ = setup_world_effect(tmp_path)
    try:
        prepared = ScenarioContractService(repo).prepare_generation(run["module_id"])
        evidence = next(item for item in prepared.evidence if item.entity_candidates)
        contract = repo.get_module_run_contract_binding(run["id"])["contract"]
        entity = contract.entities[0].model_copy(update={"source_refs": (evidence.source_ref(),)})
        contract = contract.model_copy(update={"entities": (entity,)})
        catalog = {evidence.source_block_id: evidence.entity_candidates}
        validate_authoring_entities(contract, catalog)
        operator = next(
            item for item in contract.operators if item.operator_id == "interview-witness"
        )
        wrong_state = operator.model_copy(
            update={
                "success_commands": (
                    WorldCommand(
                        kind="set_fact", path=f"entities.{entity.entity_id}.condition", value="read"
                    ),
                )
            }
        )
        with pytest.raises(ValueError, match="shadow fact"):
            validate_authoring_entities(
                contract.model_copy(update={"operators": (wrong_state,)}), catalog
            )
        with pytest.raises(ValueError, match="not offered"):
            validate_authoring_entities(contract, {})
        candidate = evidence.entity_candidates[0]
        with pytest.raises(ValueError, match="dimension was not offered"):
            validate_authoring_entities(
                contract,
                {
                    evidence.source_block_id: (
                        candidate.model_copy(update={"state_dimensions": ()}),
                    )
                },
            )
        with pytest.raises(ValueError, match="visibility is broader"):
            validate_authoring_entities(
                contract,
                {
                    evidence.source_block_id: (
                        candidate.model_copy(update={"state_visibility_floor": "secret"}),
                    )
                },
            )
        different = entity.model_copy(
            update={"entity_id": "different-source", "module_entity_id": "other-module-id"}
        )
        unbound = entity.model_copy(update={"entity_id": "unbound", "module_entity_id": None})
        result = converge_scenario_entity_identities(
            contract.model_copy(update={"entities": (entity, different, unbound)})
        )
        assert len(result.contract.entities) == 3
        assert result.replacements == {}
    finally:
        repo.connection.close()


def test_ir_assembly_preserves_explicit_module_identity():
    ir = batch("block-1").model_copy(
        update={
            "entities": (
                IrEntity(
                    id="local-log",
                    module_entity_id="source-log",
                    type="item",
                    title="日志",
                    source_block_ids=("block-1",),
                ),
            )
        }
    )
    result = ScenarioIrAssembler().assemble(
        (ir,),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="catalog-test",
        source_version=1,
        ruleset_id="coc7",
        title="Catalog",
        corpus_truncated=False,
    )
    assert result.contract.entities[0].module_entity_id == "source-log"


def test_candidate_metadata_counts_toward_partition_budget(tmp_path):
    repo, _, run, _, _, _ = setup_world_effect(tmp_path)
    try:
        prepared = ScenarioContractService(repo).prepare_generation(run["module_id"])
        item = next(item for item in prepared.evidence if item.entity_candidates)
        candidate = item.entity_candidates[0].model_copy(update={"description": "x" * 500})
        oversized = item.model_copy(
            update={"text": "x" * 16000, "entity_candidates": (candidate,) * 64}
        )
        with pytest.raises(ValueError, match="partition budget"):
            ConstrainedScenarioContractAuthoringAdapter.partitions((oversized,))
    finally:
        repo.connection.close()


def test_selected_profile_supplies_dimensions_before_materialization(tmp_path):
    from ai_kp.application.scenario_authoring_entity_catalog import authoring_entity_catalog
    from ai_kp.platform.scenes.builtin_setting_packs import US_1920S
    from ai_kp.platform.scenes.setting_profiles import ConfiguredEntityBinding
    from tests.test_scenario_entity_identity import setup_identity
    from tests.test_setting_profiles import _document

    repo, module_id, _, graph, _ = setup_identity(tmp_path)
    try:
        module = repo.get_module(module_id)
        run = repo.start_campaign_module_run(
            campaign_id=module["campaign_id"],
            module_id=module_id,
            current_scene_key=None,
            active_spoiler_tags=[],
            state={},
            started_by_member_id=None,
        )
        archetype = next(item for item in US_1920S.entity_archetypes if item.entity_kind == "item")
        document = _document().model_copy(
            update={
                "entity_bindings": (
                    ConfiguredEntityBinding(
                        module_entity_id=graph["id"], archetype_id=archetype.archetype_id
                    ),
                )
            }
        )
        profile = repo.create_module_setting_profile(
            module_id=module_id,
            title="Selected test profile",
            setting_pack_id=US_1920S.setting_pack_id,
            setting_pack_version=US_1920S.pack_version,
            document=document.model_dump(mode="json"),
            member_id=None,
        )
        unselected = authoring_entity_catalog(repo, module, {"chunk_graph"})
        assert unselected["chunk_graph"][0].state_dimensions == ()
        repo.set_module_run_setting_selection(
            run["id"],
            expected_run_version=run["version"],
            profile_id=profile["id"],
            profile_version=profile["version"],
            settlement_id=document.settlements[0].settlement_id,
            reason="Use explicit profile",
            member_id=None,
        )
        catalog = authoring_entity_catalog(repo, module, {"chunk_graph"})
        assert catalog["chunk_graph"][0].state_dimensions == tuple(archetype.state_dimensions)
        assert repo.list_campaign_world_entities(module["campaign_id"]) == []
    finally:
        repo.connection.close()
