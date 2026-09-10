from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_kp.infrastructure.database.scenario_run_states import ScenarioRunStateRepository
from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    ScenarioContract,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel, operator_outcome_path
from ai_kp.platform.resolution.location_travel import (
    materialize_location_travel_operators,
)
from ai_kp.platform.resolution.playability import ScenarioPlayabilityAnalyzer
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.world_settlement import settle_world_commands

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "scenario_contracts"
    / "open_investigation.json"
)


def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def analyze(value: dict):
    compiled = ScenarioContractCompiler().compile(value)
    assert compiled.contract is not None
    return ScenarioPlayabilityAnalyzer().analyze(compiled.contract)


def test_generic_contract_produces_all_six_playability_witnesses() -> None:
    report = analyze(payload())

    assert report.ready is True
    assert len(report.proofs) == 6
    assert {item.status for item in report.proofs} == {"passed"}
    assert "resolved" in report.proof("ending_reachability").witness[0]


def test_supporting_clue_facts_do_not_create_unread_state_products() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "supporting-clue-projection",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Supporting clue projection",
        "clues": [{
            "clue_id": "supporting",
            "title": "Supporting",
            "importance": "supporting",
            "discovery_operator_ids": ["find-supporting"],
            "fact_path": "supporting_found",
            "fact_value": True,
            "public_content": ["A supporting detail is visible."],
        }, {
            "clue_id": "core",
            "title": "Core",
            "importance": "core",
            "discovery_operator_ids": ["find-core"],
            "fact_path": "core_found",
            "fact_value": True,
            "public_content": ["The core clue is visible."],
        }],
        "operators": [{
            "operator_id": "find-supporting",
            "title": "Find supporting",
            "policy": "automatic",
            "automatic_information": ["A supporting detail is visible."],
            "success_commands": [{
                "kind": "set_fact", "path": "supporting_found", "value": True,
            }],
        }, {
            "operator_id": "find-core",
            "title": "Find core",
            "policy": "automatic",
            "automatic_information": ["The core clue is visible."],
            "success_commands": [{
                "kind": "set_fact", "path": "core_found", "value": True,
            }],
        }],
    })
    analyzer = ScenarioPlayabilityAnalyzer()
    paths = analyzer._relevant_state_paths(contract)
    exploration = analyzer._explore(contract)

    assert "facts.supporting_found" not in paths
    assert "facts.core_found" in paths
    assert len(exploration.states) == 2
    assert exploration.reached_operator_outcomes.keys() >= {
        ("find-supporting", "success"),
        ("find-core", "success"),
    }

    condition_reader = ActionOperator(
        operator_id="read-supporting",
        title="Read supporting",
        policy="automatic",
        preconditions=(
            StateCondition(
                path="facts.supporting_found", operator="eq", value=True
            ),
        ),
        success_commands=(
            WorldCommand(kind="set_fact", path="reader_finished", value=True),
        ),
    )
    contract_with_reader = contract.model_copy(
        update={"operators": (*contract.operators, condition_reader)}
    )
    assert "facts.supporting_found" in analyzer._relevant_state_paths(
        contract_with_reader
    )


def test_supporting_clue_condition_is_retained_in_target_producer_slice() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "supporting-condition-chain",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Supporting condition chain",
        "clues": [{
            "clue_id": "supporting",
            "title": "Supporting",
            "importance": "supporting",
            "discovery_operator_ids": ["find-supporting"],
            "fact_path": "supporting_found",
            "fact_value": True,
            "public_content": ["The supporting fact is visible."],
        }, {
            "clue_id": "core",
            "title": "Core",
            "importance": "core",
            "discovery_operator_ids": ["find-core"],
            "fact_path": "core_found",
            "fact_value": True,
            "public_content": ["The core fact is visible."],
        }],
        "operators": [{
            "operator_id": "find-supporting",
            "title": "Find supporting",
            "policy": "automatic",
            "automatic_information": ["The supporting fact is visible."],
            "success_commands": [{
                "kind": "set_fact", "path": "supporting_found", "value": True,
            }],
        }, {
            "operator_id": "find-core",
            "title": "Find core",
            "policy": "automatic",
            "preconditions": [{
                "path": "facts.supporting_found", "operator": "eq", "value": True,
            }],
            "automatic_information": ["The core fact is visible."],
            "success_commands": [{
                "kind": "set_fact", "path": "core_found", "value": True,
            }],
        }],
    })

    report = ScenarioPlayabilityAnalyzer(maximum_states=4).analyze(contract)

    assert report.exploration_complete is True
    assert report.proof("core_clue_discoverability").status == "passed"
    assert "find-supporting.success" in report.proof(
        "core_clue_discoverability"
    ).witness[0]


def test_backward_slice_closes_cross_component_producer_chain() -> None:
    irrelevant = 12
    contract = ScenarioContract.model_validate({
        "contract_id": "cross-component-producer-chain",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Cross component producer chain",
        "initial_facts": {
            **{f"noise_{index}": False for index in range(irrelevant)},
            "started": False,
        },
        "clues": [{
            "clue_id": "core",
            "title": "Core",
            "importance": "core",
            "discovery_operator_ids": ["find-core"],
            "fact_path": "core_found",
            "fact_value": True,
            "public_content": ["The core fact is visible."],
        }],
        "operators": [
            *[{
                "operator_id": f"noise-{index}",
                "title": f"Noise {index}",
                "policy": "automatic",
                "preconditions": [{
                    "path": f"facts.noise_{index}", "operator": "eq", "value": False,
                }],
                "success_commands": [{
                    "kind": "set_fact", "path": f"noise_{index}", "value": True,
                }],
            } for index in range(irrelevant)],
            {
                "operator_id": "start-chain",
                "title": "Start chain",
                "policy": "automatic",
                "success_commands": [{
                    "kind": "set_fact", "path": "started", "value": True,
                }],
            }, {
                "operator_id": "unlock-chain",
                "title": "Unlock chain",
                "policy": "automatic",
                "preconditions": [{
                    "path": "facts.started", "operator": "eq", "value": True,
                }],
                "success_commands": [{
                    "kind": "set_fact", "path": "unlocked", "value": True,
                }],
            }, {
                "operator_id": "find-core",
                "title": "Find core",
                "policy": "automatic",
                "preconditions": [{
                    "path": "facts.unlocked", "operator": "eq", "value": True,
                }],
                "automatic_information": ["The core fact is visible."],
                "success_commands": [{
                    "kind": "set_fact", "path": "core_found", "value": True,
                }],
            },
        ],
    })

    report = ScenarioPlayabilityAnalyzer(maximum_states=8).analyze(contract)

    assert report.exploration_complete is True
    assert report.explored_state_count == 4
    assert report.proof("core_clue_discoverability").status == "passed"
    assert "start-chain.success -> unlock-chain.success -> find-core.success" in (
        report.proof("core_clue_discoverability").witness[0]
    )


def test_backward_slice_retains_semantic_trigger_condition_and_event_producers() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "semantic-trigger-producer-chain",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Semantic trigger producer chain",
        "operators": [{
            "operator_id": "authorize",
            "title": "Authorize",
            "policy": "automatic",
            "success_commands": [{
                "kind": "set_fact", "path": "authorized", "value": True,
            }],
        }, {
            "operator_id": "signal",
            "title": "Signal",
            "policy": "automatic",
            "success_commands": [{"kind": "emit_event", "event_type": "signal"}],
        }],
        "trigger_rules": [{
            "trigger_id": "resolve-signal",
            "event_type": "signal",
            "conditions": [{
                "path": "facts.authorized", "operator": "eq", "value": True,
            }],
            "commands": [{
                "kind": "set_fact", "path": "resolved", "value": True,
            }],
        }],
        "endings": [{
            "ending_id": "resolved",
            "title": "Resolved",
            "all_conditions": [{
                "path": "facts.resolved", "operator": "eq", "value": True,
            }],
        }],
    })

    report = ScenarioPlayabilityAnalyzer(maximum_states=4).analyze(contract)

    assert report.exploration_complete is True
    assert report.proof("ending_reachability").status == "passed"
    assert "authorize.success -> signal.success" in (
        report.proof("ending_reachability").witness[0]
    )


def test_unclassified_event_read_falls_back_to_bounded_global_exploration() -> None:
    noise_count = 8
    contract = ScenarioContract.model_validate({
        "contract_id": "unknown-event-read-fallback",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Unknown event read fallback",
        "initial_facts": {f"noise_{index}": False for index in range(noise_count)},
        "clues": [{
            "clue_id": "core",
            "title": "Core",
            "importance": "core",
            "discovery_operator_ids": ["find-core"],
            "fact_path": "core_found",
            "fact_value": True,
            "public_content": ["The core fact is visible."],
        }],
        "operators": [
            *[{
                "operator_id": f"noise-{index}",
                "title": f"Noise {index}",
                "policy": "automatic",
                "preconditions": [{
                    "path": f"facts.noise_{index}", "operator": "eq", "value": False,
                }],
                "success_commands": [{
                    "kind": "set_fact", "path": f"noise_{index}", "value": True,
                }],
            } for index in range(noise_count)],
            {
                "operator_id": "find-core",
                "title": "Find core",
                "policy": "automatic",
                "preconditions": [{
                    "path": "events.external.approved", "operator": "eq", "value": True,
                }],
                "automatic_information": ["The core fact is visible."],
                "success_commands": [{
                    "kind": "set_fact", "path": "core_found", "value": True,
                }],
            },
        ],
    })

    report = ScenarioPlayabilityAnalyzer(maximum_states=16).analyze(contract)

    assert report.explored_state_count == 16
    assert report.exploration_complete is False
    assert report.ready is False
    assert report.proof("core_clue_discoverability").status == "indeterminate"


def test_repeated_analysis_does_not_share_mutable_exploration_state() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "isolated-analysis-runs",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Isolated analysis runs",
        "operators": [{
            "operator_id": "finish",
            "title": "Finish",
            "policy": "automatic",
            "success_commands": [{
                "kind": "set_fact", "path": "finished", "value": True,
            }],
        }],
        "endings": [{
            "ending_id": "finished",
            "title": "Finished",
            "all_conditions": [{
                "path": "facts.finished", "operator": "eq", "value": True,
            }],
        }],
    })
    analyzer = ScenarioPlayabilityAnalyzer()

    first_exploration = analyzer._proof_explore(contract, {"facts.finished"})
    second_exploration = analyzer._proof_explore(contract, {"facts.finished"})

    assert first_exploration is not second_exploration
    assert first_exploration.states is not second_exploration.states
    assert first_exploration.states == second_exploration.states
    assert analyzer.analyze(contract) == analyzer.analyze(contract)


def test_explorer_rejects_the_same_out_of_bounds_resource_and_clock_batches() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "bounded-settlement",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Bounded settlement",
        "resources": [{
            "resource_id": "cash", "title": "Cash", "initial_value": 1,
            "minimum_value": 0, "maximum_value": 2,
        }],
        "clocks": [{
            "clock_id": "danger", "title": "Danger", "initial_value": 0,
            "maximum_value": 2,
        }],
        "operators": [{
            "operator_id": "overspend",
            "title": "Overspend",
            "policy": "automatic",
            "success_commands": [{
                "kind": "adjust_resource", "path": "cash", "delta": -2,
            }],
        }, {
            "operator_id": "overfill",
            "title": "Overfill",
            "policy": "automatic",
            "success_commands": [{
                "kind": "advance_clock", "clock_id": "danger", "delta": 3,
            }],
        }],
    })
    initial = contract.initial_snapshot("bounds")
    analyzer = ScenarioPlayabilityAnalyzer()

    for operator in contract.operators:
        commands = (
            WorldCommand(
                kind="emit_event",
                event_type="action_resolved",
                payload={
                    "action_id": operator.operator_id,
                    "operator_id": operator.operator_id,
                    "outcome": "success",
                },
            ),
            *operator.success_commands,
        )
        with pytest.raises(ValueError, match="outside contract bounds"):
            settle_world_commands(contract, initial, commands)

    assert analyzer.reachable_operator_outcomes(contract) == frozenset()


def test_explorer_and_repository_share_full_entity_runtime_settlement() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "entity-runtime-settlement",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Entity runtime settlement",
        "locations": [
            {"location_id": "hall", "title": "Hall"},
            {"location_id": "cellar", "title": "Cellar"},
        ],
        "entities": [{
            "entity_id": "npc",
            "entity_type": "npc",
            "title": "Witness",
            "initial_status": "watching",
            "initial_location_id": "hall",
            "initial_runtime": {
                "status": "watching",
                "location_id": "hall",
                "emotional_state": "nervous",
                "memory_refs": ["met_before"],
            },
        }],
        "operators": [{
            "operator_id": "escort-witness",
            "title": "Escort witness",
            "policy": "automatic",
            "preconditions": [{
                "path": "entity_runtime.npc.emotional_state",
                "operator": "eq",
                "value": "nervous",
            }],
            "success_commands": [{
                "kind": "set_entity_status", "entity_id": "npc", "value": "safe",
            }, {
                "kind": "move_actor", "actor_id": "npc", "value": "cellar",
            }],
        }],
        "reactive_policies": [{
            "policy_id": "witness-memory",
            "entity_id": "npc",
            "rules": [{
                "rule_id": "remember-escort",
                "trigger": "after_action",
                "conditions": [{
                    "path": "entities.npc", "operator": "eq", "value": "safe",
                }],
                "commands": [{
                    "kind": "update_entity_runtime",
                    "entity_id": "npc",
                    "payload": {"add_memory_ref": "escorted"},
                }],
                "rationale": "The witness remembers the resolved escort.",
            }],
        }],
        "endings": [{
            "ending_id": "witness-safe",
            "title": "Witness safe",
            "all_conditions": [
                {"path": "entities.npc", "operator": "eq", "value": "safe"},
                {"path": "entity_runtime.npc.status", "operator": "eq", "value": "safe"},
                {"path": "entity_runtime.npc.location_id", "operator": "eq", "value": "cellar"},
                {"path": "entity_runtime.npc.memory_refs", "operator": "contains", "value": "escorted"},
            ],
        }],
    })
    initial = contract.initial_snapshot("entity-runtime")
    operator = contract.operators[0]
    commands = (
        WorldCommand(
            kind="emit_event",
            event_type="action_resolved",
            payload={
                "action_id": "escort",
                "operator_id": operator.operator_id,
                "outcome": "success",
            },
        ),
        *operator.success_commands,
    )

    pure = settle_world_commands(contract, initial, commands)
    delegated = ScenarioRunStateRepository._settle_with_reactions(
        contract, initial, commands
    )
    assert delegated == pure
    resulting = pure[1]
    assert resulting.entities["npc"] == "safe"
    assert resulting.entity_runtime["npc"].status == "safe"
    assert resulting.entity_runtime["npc"].location_id == "cellar"
    assert resulting.entity_runtime["npc"].memory_refs == ("met_before", "escorted")
    assert resulting.actor_locations["npc"] == "cellar"
    assert resulting.ending_id == "witness-safe"
    assert ("escort-witness", "success") in (
        ScenarioPlayabilityAnalyzer().reachable_operator_outcomes(contract)
    )


def test_disconnected_scene_is_reported_without_scenario_name_knowledge() -> None:
    value = payload()
    value["locations"].append({"location_id": "unlinked", "title": "Unlinked"})

    report = analyze(value)

    proof = report.proof("scene_reachability")
    assert proof.status == "failed"
    assert "unlinked" in proof.counterexamples[0]


def test_playability_reaches_linked_scene_only_through_materialized_operator() -> None:
    raw = ScenarioContract.model_validate({
        "contract_id": "real-travel-edge",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Real travel edge",
        "initial_scene_id": "hall",
        "locations": [
            {"location_id": "hall", "title": "Hall"},
            {"location_id": "archive", "title": "Archive"},
        ],
        "location_links": [{
            "from_location_id": "hall", "to_location_id": "archive",
            "one_way": True,
        }],
    })

    assert (
        ScenarioPlayabilityAnalyzer().analyze(raw).proof("scene_reachability").status
        == "failed"
    )
    canonical = materialize_location_travel_operators(raw)
    assert (
        ScenarioPlayabilityAnalyzer()
        .analyze(canonical)
        .proof("scene_reachability")
        .status
        == "passed"
    )


def test_checked_action_requires_distinct_disclosed_failure_effects() -> None:
    value = payload()
    operator = value["operators"][0]
    operator["failure_commands"] = []
    operator["skill_choices"][0]["failure_stakes"] = ""

    report = analyze(value)

    proof = report.proof("branch_consequences")
    assert proof.status == "failed"
    assert any("failure has no observable result" in item for item in proof.counterexamples)


def test_choice_with_authorized_skills_still_requires_failure_proof() -> None:
    value = payload()
    operator = value["operators"][0]
    operator["policy"] = "choice"
    operator["failure_commands"] = []

    report = analyze(value)

    proof = report.proof("branch_consequences")
    assert proof.status == "failed"
    assert any(
        "failure has no authoritative state consequence" in item
        for item in proof.counterexamples
    )


def test_failure_stakes_text_cannot_replace_an_authoritative_consequence() -> None:
    value = payload()
    operator = value["operators"][0]
    operator["failure_commands"] = []
    assert operator["skill_choices"][0]["failure_stakes"]

    report = analyze(value)

    proof = report.proof("branch_consequences")
    assert proof.status == "failed"
    assert any(
        "failure has no authoritative state consequence" in item
        for item in proof.counterexamples
    )


def test_core_clue_success_requires_fact_commit_and_concrete_public_content() -> None:
    value = payload()
    for operator in value["operators"][:2]:
        operator["automatic_information"] = []

    report = analyze(value)

    proof = report.proof("source_content_delivery")
    assert proof.status == "failed"
    assert "concrete public content" in proof.counterexamples[0]


def test_core_clue_failure_must_recover_or_reach_an_explicit_ending() -> None:
    value = payload()
    value["clues"][0]["discovery_operator_ids"] = ["search-archive"]
    search = value["operators"][0]
    search["preconditions"] = [
        {"path": "facts.case.archive_locked", "operator": "not_exists"}
    ]
    search["failure_commands"] = [
        {"kind": "set_fact", "path": "case.archive_locked", "value": True}
    ]
    value["operators"][1]["preconditions"] = [
        {"path": "facts.case.archive_locked", "operator": "not_exists"}
    ]

    report = analyze(value)

    proof = report.proof("failure_recovery")
    assert proof.status == "failed"
    assert "without recovery" in proof.counterexamples[0]


def test_core_clue_state_is_retained_when_a_later_failure_closes_its_route() -> None:
    value = payload()
    value["initial_facts"] = {"case": {"identity": True}}
    value["clues"][0]["discovery_operator_ids"] = ["search-archive"]
    search = value["operators"][0]
    search["preconditions"] = [
        {"path": "facts.case.archive_locked", "operator": "not_exists"}
    ]
    search["failure_commands"] = [
        {"kind": "set_fact", "path": "case.archive_locked", "value": True}
    ]

    proof = analyze(value).proof("failure_recovery")

    assert proof.status == "passed"
    assert any("already committed" in item for item in proof.witness)


def test_core_clue_route_and_ending_require_executable_witness_paths() -> None:
    value = payload()
    for operator in value["operators"][:2]:
        operator["preconditions"] = [
            {"path": "facts.never.created", "operator": "eq", "value": True}
        ]

    report = analyze(value)

    assert report.proof("core_clue_discoverability").status == "failed"
    assert report.proof("ending_reachability").status == "failed"


def test_ending_cannot_be_satisfied_before_the_first_player_action() -> None:
    value = payload()
    value["endings"][0]["all_conditions"] = [{
        "path": "facts.case.resolved", "operator": "not_exists",
    }]

    report = analyze(value)

    proof = report.proof("ending_reachability")
    assert proof.status == "failed"
    assert "initial state" in proof.counterexamples[0]


def test_independent_state_components_are_proved_without_cartesian_explosion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flags = 14
    value = {
        "contract_id": "bounded-state-space",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Bounded state space",
        "initial_facts": {f"flag_{index}": False for index in range(flags)},
        "operators": [
            {
                "operator_id": f"set-flag-{index}",
                "title": f"Set flag {index}",
                "policy": "automatic",
                "preconditions": [
                    {
                        "path": f"facts.flag_{index}",
                        "operator": "eq",
                        "value": False,
                    }
                ],
                "success_commands": [
                    {
                        "kind": "set_fact",
                        "path": f"flag_{index}",
                        "value": True,
                    }
                ],
            }
            for index in range(flags)
        ],
        "endings": [
            {
                "ending_id": "first-flag-ending",
                "title": "First flag reached",
                "all_conditions": [
                    {"path": "facts.flag_0", "operator": "eq", "value": True}
                ],
            }
        ],
    }

    query_call_count = 0
    state_key_query_counts: list[int] = []
    original_query_snapshot = ActionResolutionKernel.query_snapshot
    original_state_key = ScenarioPlayabilityAnalyzer._state_key

    def counted_query_snapshot(self, snapshot):
        nonlocal query_call_count
        query_call_count += 1
        return original_query_snapshot(self, snapshot)

    def counted_state_key(state, relevant_paths, kernel, once_trigger_ids):
        before = query_call_count
        key = original_state_key(
            state, relevant_paths, kernel, once_trigger_ids
        )
        state_key_query_counts.append(query_call_count - before)
        return key

    monkeypatch.setattr(
        ActionResolutionKernel, "query_snapshot", counted_query_snapshot
    )
    monkeypatch.setattr(
        ScenarioPlayabilityAnalyzer, "_state_key", staticmethod(counted_state_key)
    )

    compiled = ScenarioContractCompiler().compile(value)
    report = compiled.report.playability

    assert report.explored_state_count == 2
    assert len(state_key_query_counts) >= 2
    assert set(state_key_query_counts) == {1}
    assert report.exploration_complete is True
    assert {proof.status for proof in report.proofs} == {"passed"}
    assert report.ready is True
    assert not any(
        issue.code == "playability_state_space_indeterminate"
        for issue in compiled.report.issues
    )


def test_state_key_reuses_one_snapshot_query_without_changing_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "state-key-query-reuse",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "State key query reuse",
        "initial_scene_id": "room",
        "initial_facts": {"case": {"ready": True}},
        "locations": [{"location_id": "room", "title": "Room"}],
        "resources": [{
            "resource_id": "cash",
            "title": "Cash",
            "initial_value": 2,
            "minimum_value": 0,
            "maximum_value": 5,
        }],
        "clocks": [{
            "clock_id": "danger",
            "title": "Danger",
            "initial_value": 1,
            "maximum_value": 4,
        }],
    })
    state = contract.initial_snapshot("state-key-query").model_copy(update={
        "events": (
            {
                "type": "trigger_fired",
                "payload": {"trigger_id": "once-rule"},
            },
            {
                "type": "trigger_fired",
                "payload": {"trigger_id": "repeatable-rule"},
            },
            {
                "type": "pressure_stage_reached",
                "payload": {"pressure_stage_id": "danger:urgent"},
            },
        )
    })
    relevant_paths = frozenset({
        "scene_id",
        "facts.case.ready",
        "facts.case.missing",
        "resources.cash",
        "clocks.danger",
    })
    once_trigger_ids = frozenset({"once-rule"})
    kernel = ActionResolutionKernel.from_contract(contract)

    legacy_values = []
    for path in sorted(relevant_paths):
        exists, value = kernel.state_value(state, path)
        legacy_values.append((
            path,
            json.dumps(
                value if exists else {"$missing": True},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ))
    legacy_values.append((
        "$world_rule_markers",
        json.dumps(
            [
                ("pressure_stage_reached", "danger:urgent"),
                ("trigger_fired", "once-rule"),
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    ))

    query_count = 0
    original_query_snapshot = ActionResolutionKernel.query_snapshot

    def counted_query_snapshot(self, snapshot):
        nonlocal query_count
        query_count += 1
        return original_query_snapshot(self, snapshot)

    monkeypatch.setattr(
        ActionResolutionKernel, "query_snapshot", counted_query_snapshot
    )

    state_key = ScenarioPlayabilityAnalyzer._state_key(
        state, relevant_paths, kernel, once_trigger_ids
    )

    assert state_key == tuple(legacy_values)
    assert query_count == 1


def test_irrelevant_audit_ledgers_do_not_expand_the_proof_state_product() -> None:
    operator_count = 40
    value = {
        "contract_id": "projected-audit-ledgers",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Projected audit ledgers",
        "operators": [
            {
                "operator_id": f"record-{index}",
                "title": f"Record {index}",
                "policy": "automatic",
                "success_commands": [
                    {
                        "kind": "set_fact",
                        "path": f"action_goals.record_{index}.achieved",
                        "value": True,
                    }
                ],
            }
            for index in range(operator_count)
        ],
        "endings": [
            {
                "ending_id": "recorded-ending",
                "title": "Recorded ending",
                "all_conditions": [
                    {
                        "path": operator_outcome_path("record-0"),
                        "operator": "eq",
                        "value": "success",
                    }
                ],
            }
        ],
    }
    compiled = ScenarioContractCompiler().compile(value)
    assert compiled.contract is not None

    report = ScenarioPlayabilityAnalyzer(maximum_states=10).analyze(
        compiled.contract
    )

    assert report.exploration_complete is True
    assert report.explored_state_count == 2
    assert report.ready is True
    assert report.proof("ending_reachability").status == "passed"
