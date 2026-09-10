from __future__ import annotations

from copy import deepcopy

from ai_kp.platform.resolution.planning import BoundedTaskPlanner, PlanRequest
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from tests.scenario_contract_testkit import source_bound_payload


def delegation_payload() -> dict:
    return source_bound_payload({
        "contract_id": "fixture-delegation",
        "schema_version": 1,
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Generic delegation plan",
        "initial_scene_id": "market",
        "locations": [
            {
                "location_id": "market",
                "title": "Market",
            }
        ],
        "initial_facts": {},
        "resources": [
            {
                "resource_id": "cash",
                "title": "Available cash",
                "initial_value": 1,
                "minimum_value": 0,
                "maximum_value": 10,
            }
        ],
        "clocks": [
            {
                "clock_id": "elapsed",
                "title": "Elapsed time",
                "initial_value": 0,
                "maximum_value": 10,
            }
        ],
        "operators": [
            {
                "operator_id": "earn-funds",
                "title": "Earn enough funds",
                "intent_hints": ["临时工作", "筹钱", "赚钱"],
                "policy": "required_check",
                "skill_choices": [
                    {
                        "skill_key": "accounting",
                        "reason": "Find legitimate paid work.",
                        "allow_push": False,
                        "failure_stakes": "Time passes without earning the required funds.",
                    },
                    {
                        "skill_key": "luck",
                        "reason": "Pursue a risky windfall.",
                        "allow_push": False,
                        "failure_stakes": "Time passes without earning the required funds.",
                    },
                ],
                "success_commands": [
                    {"kind": "adjust_resource", "path": "cash", "delta": 2},
                    {"kind": "advance_clock", "clock_id": "elapsed", "delta": 1},
                ],
                "failure_commands": [
                    {"kind": "advance_clock", "clock_id": "elapsed", "delta": 1}
                ],
            },
            {
                "operator_id": "hire-agent",
                "title": "Hire a local agent",
                "intent_hints": ["雇佣代理", "雇人调查"],
                "policy": "automatic",
                "preconditions": [
                    {"path": "resources.cash", "operator": "gte", "value": 3}
                ],
                "success_commands": [
                    {"kind": "adjust_resource", "path": "cash", "delta": -3},
                    {"kind": "set_fact", "path": "agent.hired", "value": True},
                ],
            },
            {
                "operator_id": "delegate-search",
                "title": "Delegate the investigation",
                "intent_hints": ["委托调查", "让别人调查"],
                "policy": "automatic",
                "preconditions": [
                    {"path": "facts.agent.hired", "operator": "eq", "value": True}
                ],
                "success_commands": [
                    {"kind": "advance_clock", "clock_id": "elapsed", "delta": 2},
                    {"kind": "set_fact", "path": "case.report_received", "value": True},
                ],
            },
        ],
        "task_methods": [
            {
                "method_id": "fund-hire-delegate",
                "task_key": "obtain-investigation-report",
                "title": "Earn, hire, and delegate",
                "intent_hints": [
                    "筹钱后雇人调查",
                    "先赚钱再委托调查",
                    "赚钱",
                    "雇人",
                    "调查",
                ],
                "steps": [
                    {"step_id": "earn", "operator_id": "earn-funds"},
                    {
                        "step_id": "hire",
                        "operator_id": "hire-agent",
                        "depends_on": ["earn"],
                    },
                    {
                        "step_id": "delegate",
                        "operator_id": "delegate-search",
                        "depends_on": ["hire"],
                        "actor_binding": "agent",
                    },
                ],
            }
        ],
        "endings": [
            {
                "ending_id": "report-obtained",
                "title": "Report obtained",
                "all_conditions": [
                    {
                        "path": "facts.case.report_received",
                        "operator": "eq",
                        "value": True,
                    }
                ],
            }
        ],
    }, source_block_id="delegation-source")


def compile_contract(payload: dict):
    result = ScenarioContractCompiler().compile(payload)
    assert result.report.valid is True
    assert result.contract is not None
    return result.contract


def request(**changes: object) -> PlanRequest:
    values = {
        "plan_id": "plan-1",
        "action_id": "action-1",
        "actor_id": "investigator-1",
        "task_key": "obtain-investigation-report",
        "method_id": "fund-hire-delegate",
        "actor_bindings": {"agent": "npc-agent-1"},
        "requested_skill_keys": {"earn": "luck"},
    }
    values.update(changes)
    return PlanRequest.model_validate(values)


def test_planner_composes_economy_hiring_and_delegation_without_writes() -> None:
    contract = compile_contract(delegation_payload())
    starting = contract.initial_snapshot("run-1")
    planner = BoundedTaskPlanner(contract)

    preview = planner.preview(starting, request())
    repeated = planner.preview(starting, request())

    assert [step.step_id for step in preview.steps] == ["earn", "hire", "delegate"]
    assert preview.steps[0].preview.selected_skill_key == "luck"
    assert preview.steps[2].actor_id == "npc-agent-1"
    assert preview.check_step_ids == ("earn",)
    assert preview.resulting_snapshot.resources["cash"] == 0
    assert preview.resulting_snapshot.clocks["elapsed"] == 3
    assert preview.resulting_snapshot.facts["agent"]["hired"] is True
    assert preview.resulting_snapshot.status == "completed"
    assert preview.resulting_snapshot.ending_id == "report-obtained"
    assert preview.completes_scenario is True
    assert starting.run_version == 0
    assert starting.facts == {}
    assert preview.preview_hash == repeated.preview_hash


def test_planner_rejects_a_later_step_when_success_path_cannot_pay_cost() -> None:
    payload = delegation_payload()
    payload["operators"][0]["success_commands"][0]["delta"] = 1
    contract = compile_contract(payload)

    try:
        BoundedTaskPlanner(contract).preview(contract.initial_snapshot("run-1"), request())
    except ValueError as exc:
        assert "hire" in str(exc)
        assert "blocked" in str(exc)
    else:
        raise AssertionError("Plan with insufficient resources should be rejected")


def test_planner_requires_explicit_non_initiator_actor_binding() -> None:
    contract = compile_contract(delegation_payload())

    try:
        BoundedTaskPlanner(contract).preview(
            contract.initial_snapshot("run-1"), request(actor_bindings={})
        )
    except ValueError as exc:
        assert "actor binding: agent" in str(exc)
    else:
        raise AssertionError("Missing actor binding should be rejected")


def test_contract_rejects_method_cycles_and_unknown_condition_roots() -> None:
    cyclic = delegation_payload()
    cyclic["task_methods"][0]["steps"][0]["depends_on"] = ["delegate"]
    cycle_result = ScenarioContractCompiler().compile(cyclic)
    assert cycle_result.contract is None
    assert cycle_result.report.valid is False
    assert "dependency cycle" in cycle_result.report.issues[0].message.lower()

    bad_condition = deepcopy(delegation_payload())
    bad_condition["task_methods"][0]["preconditions"] = [
        {"path": "model_guess.available", "operator": "eq", "value": True}
    ]
    condition_result = ScenarioContractCompiler().compile(bad_condition)
    assert condition_result.contract is not None
    assert condition_result.report.valid is False
    assert "unknown_condition_root" in {
        issue.code for issue in condition_result.report.issues
    }
