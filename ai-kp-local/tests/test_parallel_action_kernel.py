from __future__ import annotations

from ai_kp.platform.resolution.kernel import operator_outcome_path
from ai_kp.platform.resolution.parallel import (
    ParallelActionKernel,
    ParallelIntent,
    ParallelSettlementRequest,
)
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.rulesets.coc7.scenario_effects import coc7_scenario_effect_catalog
from tests.scenario_contract_testkit import source_bound_payload


def parallel_contract():
    payload = {
        "contract_id": "fixture-parallel",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Parallel action fixture",
        "resources": [
            {
                "resource_id": "shared_tokens",
                "title": "Shared tokens",
                "initial_value": 1,
                "minimum_value": 0,
            }
        ],
        "entities": [
            {
                "entity_id": "key",
                "entity_type": "item",
                "title": "Key",
                "initial_status": "available",
            }
        ],
        "operators": [
            {
                "operator_id": "find-mark",
                "title": "Find a hidden mark",
                "policy": "required_check",
                "skill_choices": [
                    {
                        "skill_key": "spot_hidden",
                        "reason": "Notice the faint mark.",
                        "allow_push": False,
                        "failure_stakes": "The mark remains hidden for this attempt.",
                    },
                    {
                        "skill_key": "art",
                        "reason": "Recognize the drawing technique.",
                        "allow_push": False,
                        "failure_stakes": "The technique is misidentified for this attempt.",
                    },
                ],
                "success_commands": [
                    {"kind": "set_fact", "path": "batch.mark_found", "value": True}
                ],
                "failure_commands": [
                    {"kind": "set_fact", "path": "batch.mark_missed", "value": True}
                ],
            },
            {
                "operator_id": "open-panel",
                "title": "Open the panel",
                "policy": "automatic",
                "success_commands": [
                    {"kind": "set_fact", "path": "batch.panel_open", "value": True}
                ],
            },
            {
                "operator_id": "claim-key-a",
                "title": "First claim on the key",
                "policy": "automatic",
                "success_commands": [
                    {"kind": "set_entity_status", "entity_id": "key", "value": "held-a"}
                ],
            },
            {
                "operator_id": "claim-key-b",
                "title": "Second claim on the key",
                "policy": "automatic",
                "success_commands": [
                    {"kind": "set_entity_status", "entity_id": "key", "value": "held-b"}
                ],
            },
            {
                "operator_id": "spend-token",
                "title": "Spend a shared token",
                "policy": "automatic",
                "success_commands": [
                    {"kind": "adjust_resource", "path": "shared_tokens", "delta": -1}
                ],
            },
            {
                "operator_id": "observe-quietly",
                "title": "Observe without changing the world",
                "policy": "automatic",
            },
        ],
        "endings": [
            {
                "ending_id": "discovered",
                "title": "Discovery complete",
                "all_conditions": [
                    {"path": "facts.batch.mark_found", "operator": "eq", "value": True},
                    {"path": "facts.batch.panel_open", "operator": "eq", "value": True},
                ],
            }
        ],
    }
    result = ScenarioContractCompiler().compile(
        source_bound_payload(payload, source_block_id="parallel-source")
    )
    assert result.report.valid is True
    assert result.contract is not None
    return result.contract


def intent(action_id: str, actor_id: str, operator_id: str, **changes: object):
    values = {
        "action_id": action_id,
        "actor_id": actor_id,
        "operator_id": operator_id,
    }
    values.update(changes)
    return ParallelIntent.model_validate(values)


def test_parallel_batch_waits_for_checks_and_exposes_selected_skill() -> None:
    contract = parallel_contract()
    request = ParallelSettlementRequest(
        batch_id="batch-1",
        intents=(
            intent("a", "pc-a", "find-mark", requested_skill_key="art"),
            intent("b", "pc-b", "open-panel"),
        ),
    )

    result = ParallelActionKernel(contract).preview(
        contract.initial_snapshot("run-1"), request
    )

    assert result.status == "awaiting_checks"
    assert result.actions[0].preview.selected_skill_key == "art"
    assert result.commands == ()
    assert result.resulting_snapshot.run_version == 0


def test_compatible_parallel_effects_commit_as_one_batch_and_one_ending() -> None:
    contract = parallel_contract()
    snapshot = contract.initial_snapshot("run-1")
    first = intent("a", "pc-a", "find-mark", outcome="success", priority=5)
    second = intent("b", "pc-b", "open-panel")
    kernel = ParallelActionKernel(contract)

    result = kernel.preview(
        snapshot, ParallelSettlementRequest(batch_id="batch-1", intents=(second, first))
    )
    reordered = kernel.preview(
        snapshot, ParallelSettlementRequest(batch_id="batch-1", intents=(first, second))
    )

    assert result.status == "ready"
    assert result.resulting_snapshot.status == "completed"
    assert result.resulting_snapshot.ending_id == "discovered"
    assert result.resulting_snapshot.run_version == 1
    assert snapshot.facts == {}
    assert result.settlement_hash == reordered.settlement_hash
    query = kernel.kernel.query_snapshot(result.resulting_snapshot)
    assert query.state_value(operator_outcome_path("find-mark")) == (True, "success")
    assert query.state_value(operator_outcome_path("open-panel")) == (True, "success")
    assert [
        command.event_type
        for command in result.commands
        if command.kind == "emit_event"
    ] == ["action_resolved", "action_resolved"]


def test_parallel_ruleset_effects_are_bound_to_each_originating_actor() -> None:
    payload = parallel_contract().model_dump(mode="json")
    for operator in payload["operators"][:2]:
        operator["success_commands"].append(
            {
                "kind": "apply_ruleset_effect",
                "event_type": "san_loss",
                "payload": {"loss": "1"},
            }
        )
    compiled = ScenarioContractCompiler(coc7_scenario_effect_catalog()).compile(payload)
    assert compiled.contract is not None
    result = ParallelActionKernel(compiled.contract).preview(
        compiled.contract.initial_snapshot("run-effects"),
        ParallelSettlementRequest(
            batch_id="effects",
            intents=(
                intent("a", "pc-a", "find-mark", outcome="success"),
                intent("b", "pc-b", "open-panel"),
            ),
        ),
    )

    effect_actors = [
        command.actor_id
        for command in result.commands
        if command.kind == "apply_ruleset_effect"
    ]
    assert effect_actors == ["pc-a", "pc-b"]


def test_parallel_batch_preserves_an_exact_pushed_failure_branch() -> None:
    contract = parallel_contract()
    payload = contract.model_dump(mode="json")
    operator = payload["operators"][0]
    operator["skill_choices"][0]["allow_push"] = True
    operator["skill_choices"][0]["pushed_failure_stakes"] = (
        "The failed push exposes the investigator to a lasting consequence."
    )
    operator["outcome_branches"] = [
        {
            "outcome_key": "pushed_failure",
            "commands": [
                {
                    "kind": "set_fact",
                    "path": "batch.push_cost_applied",
                    "value": True,
                }
            ],
        }
    ]
    exact_contract = type(contract).model_validate(payload)
    result = ParallelActionKernel(exact_contract).preview(
        exact_contract.initial_snapshot("run-exact-outcome"),
        ParallelSettlementRequest(
            batch_id="exact-outcome",
            intents=(
                intent(
                    "a",
                    "pc-a",
                    "find-mark",
                    requested_skill_key="spot_hidden",
                    outcome="pushed_failure",
                ),
                intent("b", "pc-b", "open-panel"),
            ),
        ),
    )

    assert result.status == "ready"
    assert result.actions[0].outcome == "pushed_failure"
    assert result.resulting_snapshot.facts["batch"] == {
        "panel_open": True,
        "push_cost_applied": True,
    }
    query = ParallelActionKernel(exact_contract).kernel.query_snapshot(
        result.resulting_snapshot
    )
    assert query.state_value(operator_outcome_path("find-mark")) == (
        True,
        "pushed_failure",
    )


def test_parallel_batch_rejects_an_outcome_the_contract_did_not_authorize() -> None:
    contract = parallel_contract()
    snapshot = contract.initial_snapshot("run-unknown-outcome")

    result = ParallelActionKernel(contract).preview(
        snapshot,
        ParallelSettlementRequest(
            batch_id="unknown-outcome",
            intents=(
                intent("a", "pc-a", "find-mark", outcome="invented-tier"),
                intent("b", "pc-b", "open-panel"),
            ),
        ),
    )

    assert result.status == "blocked"
    assert result.commands == ()
    assert result.resulting_snapshot == snapshot
    assert result.conflicts[0].startswith("a:outcome:")


def test_conflicting_exclusive_effects_never_partially_apply() -> None:
    contract = parallel_contract()
    snapshot = contract.initial_snapshot("run-1")
    request = ParallelSettlementRequest(
        batch_id="batch-1",
        intents=(
            intent("a", "pc-a", "claim-key-a"),
            intent("b", "pc-b", "claim-key-b"),
        ),
    )

    result = ParallelActionKernel(contract).preview(snapshot, request)

    assert result.status == "conflict"
    assert result.conflicts == ("entity:key:a:b",)
    assert result.resulting_snapshot.entities["key"] == "available"
    assert result.commands == ()


def test_shared_resource_overcommit_blocks_the_entire_batch() -> None:
    contract = parallel_contract()
    snapshot = contract.initial_snapshot("run-1")
    request = ParallelSettlementRequest(
        batch_id="batch-1",
        intents=(
            intent("a", "pc-a", "spend-token"),
            intent("b", "pc-b", "spend-token"),
        ),
    )

    result = ParallelActionKernel(contract).preview(snapshot, request)

    assert result.status == "blocked"
    assert "outside contract bounds" in result.conflicts[0]
    assert result.resulting_snapshot.resources["shared_tokens"] == 1
    assert result.commands == ()
