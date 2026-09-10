from __future__ import annotations

from copy import deepcopy

import pytest

from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.world_expansion_contract import (
    WorldExpansionContractProposal,
    WorldExpansionContractValidator,
)
from tests.test_bounded_planning import delegation_payload


def base_contract():
    payload = deepcopy(delegation_payload())
    payload["initial_scene_id"] = "market"
    payload["locations"] = [{"location_id": "market", "title": "Market"}]
    source_ref = {
        "source_block_id": "delegation-source",
        "document_id": "delegation-fixture",
    }
    for group in (
        "locations",
        "location_links",
        "resources",
        "clocks",
        "operators",
        "task_methods",
        "endings",
    ):
        for item in payload.get(group, []):
            item["source_refs"] = [dict(source_ref)]
    result = ScenarioContractCompiler().compile(payload)
    assert result.contract is not None
    assert result.report.release_ready is True
    return result.contract


def proposal_payload(contract) -> dict:
    compiler = ScenarioContractCompiler()
    prefix = "expansion.side_route."
    return {
        "proposal_id": "side_route",
        "base_contract_id": contract.contract_id,
        "base_source_version": contract.source_version,
        "base_contract_hash": compiler.contract_hash(contract),
        "base_state_version": 0,
        "confidence": "high",
        "assumptions": ["A public records office plausibly operates in this settlement."],
        "rationale": "Adds a costly alternate research route without replacing source facts.",
        "records": {
            "locations": [
                {"location_id": prefix + "office", "title": "Public records office"}
            ],
            "location_links": [
                {
                    "from_location_id": "market",
                    "to_location_id": prefix + "office",
                    "one_way": True,
                }
            ],
            "operators": [
                {
                    "operator_id": prefix + "request-records",
                    "title": "Request public records",
                    "policy": "required_check",
                    "skill_choices": [
                        {
                            "skill_key": "coc7.law",
                            "reason": "Use the correct public-record procedure.",
                            "allow_push": False,
                            "failure_stakes": (
                                "The office records the failed request and delays another attempt."
                            ),
                        }
                    ],
                    "always_commands": [
                        {
                            "kind": "advance_clock",
                            "clock_id": "elapsed",
                            "delta": 1,
                        }
                    ],
                    "success_commands": [
                        {
                            "kind": "set_fact",
                            "path": prefix + "records_obtained",
                            "value": True,
                        },
                    ],
                    "failure_commands": [
                        {
                            "kind": "set_fact",
                            "path": prefix + "records_delayed",
                            "value": True,
                        }
                    ],
                }
            ],
            "task_methods": [
                {
                    "method_id": prefix + "research",
                    "task_key": "obtain-public-records",
                    "title": "Research through public records",
                    "steps": [
                        {
                            "step_id": "request",
                            "operator_id": prefix + "request-records",
                        }
                    ],
                }
            ],
        },
    }


def test_full_ai_can_auto_approve_a_bounded_additive_extension() -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    payload["records"]["consequence_signals"] = [
        {
            "signal_id": "expansion.side_route.records-progress",
            "title": "Records request progress",
            "visibility": "table",
            "display_mode": "narrative",
            "public_title": "Records request",
            "bands": [
                {
                    "band_id": "received",
                    "priority": 10,
                    "player_visible": True,
                    "public_label": "Records received",
                    "all_conditions": [
                        {
                            "path": "facts.expansion.side_route.records_obtained",
                            "operator": "eq",
                            "value": True,
                        }
                    ],
                }
            ],
        }
    ]
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "auto_approved"
    assert decision.blockers == ()
    assert decision.merged_contract is not None
    assert decision.merged_contract.source_version == contract.source_version + 1
    assert len(decision.merged_contract.operators) == len(contract.operators) + 2
    assert len([
        item
        for item in decision.merged_contract.operators
        if item.operator_id.startswith("travel-link-")
    ]) == 1
    assert len(decision.merged_contract.consequence_signals) == 1
    assert len(decision.merged_contract_hash or "") == 64


def test_non_full_ai_or_lower_confidence_remains_reviewable() -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    payload["confidence"] = "medium"
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="balanced"
    )

    assert decision.status == "review_required"
    assert decision.blockers == ("automation_level:balanced", "confidence:medium")
    assert decision.merged_contract is not None


def test_full_ai_cannot_auto_approve_a_check_with_only_narrated_failure() -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    payload["records"]["operators"][0]["failure_commands"] = []
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "rejected"
    assert "release_ready:branch_consequences" in decision.blockers
    assert decision.merged_contract is None


def test_full_ai_cannot_auto_approve_push_without_authoritative_consequence() -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    choice = payload["records"]["operators"][0]["skill_choices"][0]
    choice["allow_push"] = True
    choice["pushed_failure_stakes"] = "The records office permanently bars the actor."
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "rejected"
    assert "release_ready:branch_consequences" in decision.blockers
    assert decision.merged_contract is None


@pytest.mark.parametrize("policy", ("automatic", "choice"))
def test_non_check_expansion_cannot_hide_its_only_effect_in_failure(
    policy: str,
) -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    operator = payload["records"]["operators"][0]
    operator["policy"] = policy
    operator["skill_choices"] = []
    operator["always_commands"] = []
    operator["success_commands"] = []
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "rejected"
    assert (
        "unreachable_outcome_commands:expansion.side_route.request-records"
        in decision.blockers
    )
    assert "operator_without_cost:expansion.side_route.request-records" in (
        decision.blockers
    )


def test_consecutive_dynamic_overlays_preserve_prior_provenance_exemption() -> None:
    contract = base_contract()
    first = WorldExpansionContractValidator().validate(
        contract,
        WorldExpansionContractProposal.model_validate(proposal_payload(contract)),
        automation_level="ai_kp",
    )
    assert first.status == "auto_approved"
    assert first.merged_contract is not None

    second_payload = proposal_payload(first.merged_contract)
    second_payload["proposal_id"] = "second_route"

    def replace_prefix(value):
        if isinstance(value, str):
            return value.replace("expansion.side_route.", "expansion.second_route.")
        if isinstance(value, list):
            return [replace_prefix(item) for item in value]
        if isinstance(value, dict):
            return {key: replace_prefix(item) for key, item in value.items()}
        return value

    second_payload["records"] = replace_prefix(second_payload["records"])
    second_proposal = WorldExpansionContractProposal.model_validate(second_payload)
    untrusted = WorldExpansionContractValidator().validate(
        first.merged_contract,
        second_proposal,
        automation_level="ai_kp",
    )
    assert untrusted.status == "rejected"
    assert "release_ready:provenance" in untrusted.blockers

    second = WorldExpansionContractValidator().validate(
        first.merged_contract,
        second_proposal,
        automation_level="ai_kp",
        trusted_dynamic_records=(
            WorldExpansionContractProposal.model_validate(
                proposal_payload(contract)
            ).records,
        ),
    )

    assert second.status == "auto_approved"
    assert "release_ready:provenance" not in second.blockers


def test_expansion_narration_cannot_speak_as_a_source_or_secret_entity() -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    operator = payload["records"]["operators"][0]
    operator["public_setup"] = "The clerk considers the request."
    operator["narrative_cues"] = [
        {
            "outcome_key": "success",
            "public_summary": "The records request is accepted.",
            "speaker_entity_id": "source.secret-clerk",
        }
    ]
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "rejected"
    assert "source_narrative_speaker:source.secret-clerk" in decision.blockers


def test_expansion_cannot_mutate_source_facts_or_skip_explicit_costs() -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    operator = payload["records"]["operators"][0]
    operator["always_commands"] = []
    operator["success_commands"] = [
        {"kind": "set_fact", "path": "case.report_received", "value": False}
    ]
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "rejected"
    assert "source_fact_mutation:case.report_received" in decision.blockers


def test_expansion_cannot_emit_kernel_reserved_action_events() -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    operator = payload["records"]["operators"][0]
    operator["success_commands"] = [{
        "kind": "emit_event",
        "event_type": "action_resolved",
        "payload": {
            "operator_id": "delegate-search",
            "outcome": "success",
        },
    }]
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "rejected"
    assert "reserved_event_type:action_resolved" in decision.blockers


@pytest.mark.parametrize("kind", ("move_actor", "set_scene"))
def test_expansion_cannot_grant_high_authority_navigation_commands(kind: str) -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    operator = payload["records"]["operators"][0]
    operator["success_commands"] = [{
        "kind": kind,
        **(
            {"actor_id": "investigator", "value": "uncompiled-location"}
            if kind == "move_actor"
            else {"value": "uncompiled-scene"}
        ),
    }]
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "rejected"
    assert f"forbidden_command:{kind}" in decision.blockers


def test_expansion_may_move_only_the_initiator_to_a_contract_location() -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    operator = payload["records"]["operators"][0]
    operator["always_commands"] = []
    operator["success_commands"] = [{
        "kind": "move_actor",
        "actor_id": "$actor",
        "value": payload["records"]["locations"][0]["location_id"],
    }]
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "auto_approved"
    assert decision.blockers == ()


def test_expansion_is_bound_to_the_exact_contract_hash() -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    payload["base_contract_hash"] = "0" * 64
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "rejected"
    assert decision.blockers == ("base_contract_hash_mismatch",)


def test_merged_extension_is_revalidated_after_additive_copy() -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    payload["records"]["task_methods"][0]["steps"][0]["operator_id"] = (
        "expansion.side_route.missing"
    )
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "rejected"
    assert any("schema_validation" in item for item in decision.blockers)


def test_model_cannot_claim_source_provenance_or_create_dynamic_core_clues() -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    payload["records"]["locations"][0]["source_refs"] = [{
        "source_block_id": "invented",
        "document_id": "invented-document",
    }]
    payload["records"]["clues"] = [{
        "clue_id": "expansion.side_route.false-core",
        "title": "Invented core clue",
        "importance": "core",
        "discovery_operator_ids": ["expansion.side_route.request-records"],
        "fact_path": "case.source_fact",
    }]
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "rejected"
    assert "model_claimed_source_provenance" in decision.blockers
    assert "dynamic_core_clue:expansion.side_route.false-core" in decision.blockers
    assert "source_clue_fact_path:case.source_fact" in decision.blockers


def test_empty_expansion_is_rejected() -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    payload["records"] = {}
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "rejected"
    assert "empty_expansion" in decision.blockers


def test_expansion_signal_cannot_disclose_source_state_or_be_unconditional() -> None:
    contract = base_contract()
    payload = proposal_payload(contract)
    payload["records"]["consequence_signals"] = [
        {
            "signal_id": "expansion.side_route.leak",
            "title": "Hidden source truth",
            "visibility": "table",
            "display_mode": "exact",
            "source_path": "facts.case.secret_identity",
            "public_title": "Suspicious identity",
            "bands": [
                {
                    "band_id": "always",
                    "player_visible": True,
                    "public_label": "Revealed",
                }
            ],
        }
    ]
    proposal = WorldExpansionContractProposal.model_validate(payload)

    decision = WorldExpansionContractValidator().validate(
        contract, proposal, automation_level="ai_kp"
    )

    assert decision.status == "rejected"
    assert "source_signal_path:facts.case.secret_identity" in decision.blockers
    assert any(
        item.startswith("unconditional_public_signal:") for item in decision.blockers
    )
