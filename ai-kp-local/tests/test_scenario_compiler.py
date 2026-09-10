from __future__ import annotations

import json
from pathlib import Path

from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.rulesets.coc7.scenario_effects import coc7_scenario_effect_catalog
from tests.scenario_contract_testkit import without_source_refs
from tests.test_bounded_planning import delegation_payload

FIXTURES = Path(__file__).parent / "fixtures" / "scenario_contracts"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_compiler_accepts_structurally_different_generic_scenarios() -> None:
    compiler = ScenarioContractCompiler()

    timed = compiler.compile(load_fixture("timed_route.json"))
    open_investigation = compiler.compile(load_fixture("open_investigation.json"))

    assert timed.report.valid is True
    assert timed.report.unreachable_location_ids == ()
    assert len(timed.report.contract_hash or "") == 64
    assert open_investigation.report.valid is True
    assert open_investigation.report.unreachable_location_ids == ()
    assert timed.report.contract_hash != open_investigation.report.contract_hash
    operator_ids = {item.operator_id for item in open_investigation.contract.operators}
    assert {
        "search-archive",
        "interview-witness",
        "resolve-case",
    } <= operator_ids
    assert len([item for item in operator_ids if item.startswith("travel-link-")]) == 6


def test_compiler_rejects_authored_content_at_reserved_travel_identity() -> None:
    value = load_fixture("open_investigation.json")
    first = ScenarioContractCompiler().compile(value)
    assert first.contract is not None
    travel_id = next(
        item.operator_id
        for item in first.contract.operators
        if item.operator_id.startswith("travel-link-")
    )
    value["operators"].append({
        "operator_id": travel_id,
        "title": "Conflicting authored action",
        "policy": "automatic",
    })

    conflicting = ScenarioContractCompiler().compile(value)

    assert conflicting.contract is None
    assert conflicting.report.issues[0].code == "canonical_travel_conflict"


def test_missing_provenance_keeps_a_valid_contract_out_of_release() -> None:
    result = ScenarioContractCompiler().compile(
        without_source_refs(delegation_payload())
    )

    assert result.report.valid is True
    assert result.report.playability.ready is True
    assert result.report.provenance_ready is False
    assert result.report.release_ready is False
    assert "missing_provenance" in {item.code for item in result.report.issues}


def test_compiler_returns_structured_schema_errors_instead_of_raising() -> None:
    payload = load_fixture("timed_route.json")
    payload["location_links"][0]["to_location_id"] = "missing"

    result = ScenarioContractCompiler().compile(payload)

    assert result.contract is None
    assert result.report.valid is False
    assert result.report.issues[0].code == "schema_validation"
    assert "unknown location" in result.report.issues[0].message.lower()


def test_compiler_detects_unknown_condition_roots_and_conflicting_effects() -> None:
    payload = load_fixture("open_investigation.json")
    payload["operators"][0]["preconditions"] = [
        {"path": "model_guess.allowed", "operator": "eq", "value": True}
    ]
    payload["operators"][0]["success_commands"].append(
        {"kind": "set_fact", "path": "case.identity", "value": False}
    )

    result = ScenarioContractCompiler().compile(payload)

    assert result.contract is not None
    assert result.report.valid is False
    codes = {issue.code for issue in result.report.issues}
    assert "unknown_condition_root" in codes
    assert "conflicting_commands" in codes


def test_compiler_blocks_fragile_core_clues_and_warns_for_unreachable_content() -> None:
    payload = load_fixture("open_investigation.json")
    payload["locations"].append({"location_id": "isolated", "title": "Isolated"})
    payload["clues"][0]["discovery_operator_ids"] = ["search-archive"]

    result = ScenarioContractCompiler().compile(payload)

    assert result.report.valid is False
    assert result.report.unreachable_location_ids == ("isolated",)
    codes = {issue.code for issue in result.report.issues}
    assert "unreachable_locations" in codes
    assert "fragile_core_clue" in codes


def test_single_checked_core_clue_is_valid_when_every_outcome_delivers_it() -> None:
    payload = load_fixture("open_investigation.json")
    payload["clues"][0]["discovery_operator_ids"] = ["search-archive"]
    payload["operators"][0]["failure_commands"].append(
        {"kind": "set_fact", "path": "case.identity", "value": True}
    )

    result = ScenarioContractCompiler().compile(payload)

    assert result.report.valid is True
    assert "fragile_core_clue" not in {
        issue.code for issue in result.report.issues
    }


def test_unproduced_ending_condition_blocks_publication() -> None:
    payload = load_fixture("open_investigation.json")
    payload["endings"][0]["all_conditions"] = [
        {"path": "facts.never.produced", "operator": "eq", "value": True}
    ]

    result = ScenarioContractCompiler().compile(payload)

    assert result.report.valid is False
    assert "ending_condition_unproduced" in {
        issue.code for issue in result.report.issues
    }


def test_contract_hash_is_independent_of_json_key_order() -> None:
    original = load_fixture("timed_route.json")
    reversed_items = dict(reversed(list(original.items())))
    compiler = ScenarioContractCompiler()

    first = compiler.compile(original)
    second = compiler.compile(reversed_items)

    assert first.report.contract_hash == second.report.contract_hash


def test_ruleset_effects_require_a_matching_closed_catalog() -> None:
    payload = load_fixture("open_investigation.json")
    payload["operators"][0]["success_commands"].append(
        {
            "kind": "apply_ruleset_effect",
            "event_type": "san_loss",
            "payload": {"loss": "1d6"},
        }
    )

    missing = ScenarioContractCompiler().compile(payload)
    assert missing.report.valid is False
    assert "ruleset_effect_catalog_missing" in {
        item.code for item in missing.report.issues
    }

    accepted = ScenarioContractCompiler(coc7_scenario_effect_catalog()).compile(payload)
    assert accepted.report.valid is True

    payload["operators"][0]["success_commands"][-1]["actor_id"] = "chosen-victim"
    forbidden_actor = ScenarioContractCompiler(
        coc7_scenario_effect_catalog()
    ).compile(payload)
    assert forbidden_actor.report.valid is False
    assert "ruleset_effect_forbidden_actor" in {
        item.code for item in forbidden_actor.report.issues
    }
    payload["operators"][0]["success_commands"][-1].pop("actor_id")

    payload["operators"][0]["success_commands"][-1]["payload"]["loss"] = "python()"
    invalid = ScenarioContractCompiler(coc7_scenario_effect_catalog()).compile(payload)
    assert invalid.report.valid is False
    assert "invalid_ruleset_effect" in {item.code for item in invalid.report.issues}

    payload["operators"][0]["success_commands"][-1]["payload"]["loss"] = "999d9999"
    oversized = ScenarioContractCompiler(coc7_scenario_effect_catalog()).compile(payload)
    assert oversized.report.valid is False
    assert any("safety limits" in item.message for item in oversized.report.issues)


def test_ending_cannot_be_proved_by_a_player_selectable_noop() -> None:
    payload = load_fixture("open_investigation.json")
    payload["operators"].append({
        "operator_id": "declare-ending",
        "title": "Declare that the case is solved",
        "intent_hints": ["the case is solved"],
        "policy": "automatic",
    })
    from ai_kp.platform.resolution.kernel import operator_outcome_path

    payload["endings"][0]["all_conditions"] = [{
        "path": operator_outcome_path("declare-ending"),
        "operator": "eq",
        "value": "success",
    }]

    result = ScenarioContractCompiler().compile(payload)

    assert result.report.valid is False
    assert "ending_operator_has_no_causal_result" in {
        issue.code for issue in result.report.issues
    }


def test_ending_cannot_be_an_ungated_automatic_state_change() -> None:
    payload = load_fixture("open_investigation.json")
    payload["operators"].append({
        "operator_id": "instant-victory",
        "title": "Win immediately",
        "policy": "automatic",
        "success_commands": [
            {"kind": "set_fact", "path": "case.won", "value": True}
        ],
    })
    from ai_kp.platform.resolution.kernel import operator_outcome_path

    payload["endings"][0]["all_conditions"] = [{
        "path": operator_outcome_path("instant-victory"),
        "operator": "eq",
        "value": "success",
    }]

    result = ScenarioContractCompiler().compile(payload)

    assert result.report.valid is False
    assert "ungated_automatic_ending_operator" in {
        issue.code for issue in result.report.issues
    }


def test_ending_cannot_reference_an_unknown_operator_outcome_event() -> None:
    payload = load_fixture("open_investigation.json")
    from ai_kp.platform.resolution.kernel import operator_outcome_path

    payload["endings"][0]["all_conditions"] = [{
        "path": operator_outcome_path("missing-terminal-operator"),
        "operator": "eq",
        "value": "success",
    }]

    result = ScenarioContractCompiler().compile(payload)

    assert result.report.valid is False
    assert "ending_operator_unknown" in {
        issue.code for issue in result.report.issues
    }


def test_location_graph_reports_missing_explicit_player_entrypoint() -> None:
    payload = load_fixture("open_investigation.json")
    payload["initial_scene_id"] = None

    result = ScenarioContractCompiler().compile(payload)

    assert result.report.valid is True
    assert "initial_scene_missing" in {issue.code for issue in result.report.issues}


def test_location_reachability_uses_executable_scene_transitions() -> None:
    payload = load_fixture("open_investigation.json")
    payload["location_links"] = []
    payload["operators"] = [{
        "operator_id": "enter-archive",
        "title": "Enter the archive",
        "policy": "automatic",
        "preconditions": [
            {"path": "scene_id", "operator": "eq", "value": "briefing"}
        ],
        "success_commands": [{"kind": "set_scene", "value": "archive"}],
    }, {
        "operator_id": "visit-witness",
        "title": "Visit the witness",
        "policy": "automatic",
        "preconditions": [
            {"path": "scene_id", "operator": "eq", "value": "archive"}
        ],
        "success_commands": [{"kind": "set_scene", "value": "witness"}],
    }]
    payload["clues"] = []
    payload["endings"] = []

    result = ScenarioContractCompiler().compile(payload)

    assert result.report.reachable_location_ids == ("briefing", "archive", "witness")
    assert result.report.unreachable_location_ids == ("site",)
    assert "unreachable_locations" in {
        issue.code for issue in result.report.issues
    }


def test_core_clue_requires_concrete_public_success_payload() -> None:
    payload = load_fixture("open_investigation.json")
    for operator in payload["operators"]:
        operator.pop("automatic_information", None)

    result = ScenarioContractCompiler().compile(payload)

    assert result.report.valid is False
    assert "clue_discovery_has_no_public_payload" in {
        issue.code for issue in result.report.issues
    }
