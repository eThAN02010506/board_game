from __future__ import annotations

import pytest

from ai_kp.platform.resolution.action_intent_ir import (
    ActionIntentPlan,
    ActionIntentPolicyValidator,
)
from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    TaskMethod,
    WorldCommand,
)
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from tests.test_bounded_planning import delegation_payload


def _world():
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    contract = compiled.contract
    return contract, contract.initial_snapshot("intent-policy")


def _operator(operator_id: str, path: str) -> ActionOperator:
    return ActionOperator.model_validate({
        "operator_id": operator_id,
        "title": "Generic bounded step",
        "policy": "automatic",
        "success_commands": [{"kind": "set_fact", "path": path, "value": True}],
    })


def _step(
    step_id: str,
    operator_id: str,
    path: str,
    *,
    depends_on: list[str] | None = None,
    requirements: list[dict] | None = None,
) -> dict:
    resolved_requirements = requirements or [{
        "requirement_id": f"active_{step_id}",
        "category": "execution_context",
        "description": "the scenario run is active",
        "source": "state",
        "state_path": "status",
        "comparison": "eq",
        "expected_value": "active",
    }]
    return {
        "step_id": step_id,
        "operator_id": operator_id,
        "goal": f"goal {step_id}",
        "method": f"method {step_id}",
        "target": f"target {step_id}",
        "depends_on": depends_on or [],
        "requirements": resolved_requirements,
        "effects": [{
            "effect_id": f"effect_{step_id}",
            "role": "progress",
            "command_kind": "set_fact",
            "target_ref": path,
            "value": True,
            "description": f"bounded effect {step_id}",
        }],
    }


@pytest.mark.parametrize(
    ("goal", "category", "question"),
    (
        ("coordinate access", "resource", "Which committed resource pays the cost?"),
        ("prepare an identity", "tool", "Which established material supports the method?"),
        ("cross an exposed area", "effect_handler", "Which installed rule handles the consequence?"),
    ),
)
def test_missing_requirements_use_one_generic_policy(
    goal: str, category: str, question: str
) -> None:
    contract, snapshot = _world()
    path = "expansion.generic.result"
    plan = ActionIntentPlan.model_validate({
        "goal": goal,
        "steps": [_step(
            "step",
            "expansion.generic.step",
            path,
            requirements=[{
                "requirement_id": "missing",
                "category": category,
                "description": "required external condition",
                "source": "player_input",
                "question": question,
            }],
        )],
    })

    assessment = ActionIntentPolicyValidator().assess(
        plan, contract, snapshot, operators=(), task_methods=()
    )

    assert assessment.status == "possible_but_underspecified"
    assert assessment.questions == (question,)


def test_unsupported_capability_is_impossible_without_action_vocabulary() -> None:
    contract, snapshot = _world()
    plan = ActionIntentPlan.model_validate({
        "goal": "produce an outcome outside installed authority",
        "steps": [_step(
            "step",
            "expansion.generic.step",
            "expansion.generic.result",
            requirements=[{
                "requirement_id": "handler",
                "category": "effect_handler",
                "description": "authoritative effect implementation",
                "source": "unsupported",
                "question": "The active ruleset has no authoritative handler for this effect.",
            }],
        )],
    })

    assessment = ActionIntentPolicyValidator().assess(
        plan, contract, snapshot, operators=(), task_methods=()
    )

    assert assessment.status == "impossible"


def test_state_requirements_are_checked_against_the_snapshot() -> None:
    contract, snapshot = _world()
    plan = ActionIntentPlan.model_validate({
        "goal": "use an established condition",
        "steps": [_step(
            "step",
            "expansion.generic.step",
            "expansion.generic.result",
            requirements=[{
                "requirement_id": "authority",
                "category": "fact",
                "description": "an existing authoritative fact",
                "source": "state",
                "state_path": "facts.not_established",
                "comparison": "exists",
                "question": "How will this fact be established in play?",
            }],
        )],
    })

    assessment = ActionIntentPolicyValidator().assess(
        plan, contract, snapshot, operators=(), task_methods=()
    )

    assert assessment.status == "possible_but_underspecified"
    assert assessment.reason == "How will this fact be established in play?"


def test_state_reference_selection_requires_grounded_player_evidence() -> None:
    contract, snapshot = _world()
    path = "expansion.generic.result"
    requirement = {
        "requirement_id": "payment",
        "category": "resource",
        "description": "the selected established resource",
        "source": "state",
        "state_path": "resources.cash",
        "comparison": "gte",
        "expected_value": 1,
        "question": "Which established resource will you use?",
        "player_evidence": "some resource",
    }
    plan = ActionIntentPlan.model_validate({
        "goal": "use a selected state reference",
        "steps": [_step(
            "step",
            "expansion.generic.step",
            path,
            requirements=[requirement],
        )],
    })

    missing = ActionIntentPolicyValidator().assess_requirements(
        plan,
        contract,
        snapshot,
        player_intent="I use some resource.",
    )
    grounded_plan = plan.model_copy(update={
        "steps": (
            plan.steps[0].model_copy(update={
                "requirements": (
                    plan.steps[0].requirements[0].model_copy(
                        update={"player_evidence": "cash"}
                    ),
                ),
            }),
        ),
    })
    grounded = ActionIntentPolicyValidator().assess_requirements(
        grounded_plan,
        contract,
        snapshot,
        player_intent="I use cash.",
    )

    assert missing.status == "possible_but_underspecified"
    assert missing.questions == ("Which established resource will you use?",)
    assert grounded.status == "executable"


def test_dependency_graph_and_effect_bindings_are_validated_generically() -> None:
    contract, snapshot = _world()
    first_path = "expansion.generic.prepared"
    second_path = "expansion.generic.completed"
    first_id = "expansion.generic.prepare"
    second_id = "expansion.generic.execute"
    plan = ActionIntentPlan.model_validate({
        "goal": "complete a dependent procedure",
        "steps": [
            _step("prepare", first_id, first_path),
            _step(
                "execute",
                second_id,
                second_path,
                depends_on=["prepare"],
                requirements=[{
                    "requirement_id": "prepared",
                    "category": "fact",
                    "description": "the preparation result",
                    "source": "prior_step",
                    "producer_step_id": "prepare",
                    "produced_ref": first_path,
                }],
            ),
        ],
    })
    method = TaskMethod.model_validate({
        "method_id": "expansion.generic.method",
        "task_key": "generic-task",
        "title": "Generic method",
        "steps": [
            {"step_id": "prepare", "operator_id": first_id},
            {
                "step_id": "execute",
                "operator_id": second_id,
                "depends_on": ["prepare"],
            },
        ],
    })

    assessment = ActionIntentPolicyValidator().assess(
        plan,
        contract,
        snapshot,
        operators=(_operator(first_id, first_path), _operator(second_id, second_path)),
        task_methods=(method,),
    )

    assert assessment.status == "executable"


def test_declared_effect_without_matching_command_fails_closed() -> None:
    contract, snapshot = _world()
    plan = ActionIntentPlan.model_validate({
        "goal": "bounded state change",
        "steps": [_step(
            "step", "expansion.generic.step", "expansion.generic.expected"
        )],
    })

    with pytest.raises(ValueError, match="no matching authoritative command"):
        ActionIntentPolicyValidator().assess(
            plan,
            contract,
            snapshot,
            operators=(
                _operator("expansion.generic.step", "expansion.generic.different"),
            ),
            task_methods=(),
        )


def test_author_command_without_declared_intent_effect_fails_closed() -> None:
    contract, snapshot = _world()
    operator_id = "expansion.generic.step"
    expected_path = "expansion.generic.expected"
    plan = ActionIntentPlan.model_validate({
        "goal": "bounded state change",
        "steps": [_step("step", operator_id, expected_path)],
    })
    operator = _operator(operator_id, expected_path).model_copy(update={
        "failure_commands": (
            WorldCommand(
                kind="set_fact",
                path="expansion.generic.undeclared",
                value=True,
            ),
        ),
    })

    with pytest.raises(ValueError, match="undeclared state command"):
        ActionIntentPolicyValidator().assess(
            plan,
            contract,
            snapshot,
            operators=(operator,),
            task_methods=(),
        )


def test_declared_effect_cannot_be_moved_to_a_different_outcome_branch() -> None:
    contract, snapshot = _world()
    operator_id = "expansion.generic.step"
    expected_path = "expansion.generic.expected"
    plan = ActionIntentPlan.model_validate({
        "goal": "bounded state change",
        "steps": [_step("step", operator_id, expected_path)],
    })
    operator = _operator(operator_id, expected_path).model_copy(update={
        "success_commands": (),
        "failure_commands": (
            WorldCommand(kind="set_fact", path=expected_path, value=True),
        ),
    })

    with pytest.raises(
        ValueError, match="no matching authoritative command in success branch"
    ):
        ActionIntentPolicyValidator().assess(
            plan,
            contract,
            snapshot,
            operators=(operator,),
            task_methods=(),
        )


def test_intent_effect_requires_an_explicit_target() -> None:
    effect = _step("step", "expansion.generic.step", "expansion.generic.result")[
        "effects"
    ][0]
    effect.pop("target_ref")

    with pytest.raises(ValueError, match="target_ref"):
        ActionIntentPlan.model_validate({
            "goal": "bounded state change",
            "steps": [{
                **_step(
                    "step", "expansion.generic.step", "expansion.generic.result"
                ),
                "effects": [effect],
            }],
        })


def test_intent_plan_command_signature_survives_json_round_trip() -> None:
    plan = ActionIntentPlan.model_validate({
        "goal": "advance one bounded clock",
        "steps": [{
            **_step(
                "step", "expansion.generic.step", "expansion.generic.result"
            ),
            "effects": [{
                "effect_id": "elapsed",
                "role": "cost",
                "command_kind": "advance_clock",
                "target_ref": "elapsed",
                "delta": 1,
                "description": "one unit of time passes",
            }],
        }],
    })

    restored = ActionIntentPlan.model_validate_json(plan.model_dump_json())

    assert restored == plan


@pytest.mark.parametrize(
    ("effect", "command"),
    (
        (
            {
                "command_kind": "set_fact",
                "target_ref": "expansion.generic.result",
                "value": True,
            },
            {
                "kind": "set_fact",
                "path": "expansion.generic.result",
                "value": False,
            },
        ),
        (
            {
                "command_kind": "advance_clock",
                "target_ref": "elapsed",
                "delta": 1,
            },
            {"kind": "advance_clock", "clock_id": "elapsed", "delta": 2},
        ),
        (
            {
                "command_kind": "emit_event",
                "target_ref": "expansion.generic.event",
                "payload": {"result": "declared"},
            },
            {
                "kind": "emit_event",
                "event_type": "expansion.generic.event",
                "payload": {"result": "substituted"},
            },
        ),
    ),
)
def test_effect_binding_rejects_substituted_command_parameters(
    effect: dict, command: dict
) -> None:
    contract, snapshot = _world()
    operator_id = "expansion.generic.step"
    plan = ActionIntentPlan.model_validate({
        "goal": "bounded state change",
        "steps": [{
            "step_id": "step",
            "operator_id": operator_id,
            "goal": "record one exact result",
            "method": "use one exact command",
            "target": "bounded target",
            "effects": [{
                "effect_id": "effect",
                "role": "progress",
                "applies_on": "success",
                "description": "the exact authorized result",
                **effect,
            }],
        }],
    })
    operator = ActionOperator.model_validate({
        "operator_id": operator_id,
        "title": "Generic bounded step",
        "policy": "automatic",
        "success_commands": [command],
    })

    with pytest.raises(ValueError, match="no matching authoritative command"):
        ActionIntentPolicyValidator().assess(
            plan, contract, snapshot, operators=(operator,), task_methods=()
        )


def test_duplicate_step_operator_binding_is_rejected_before_set_collapse() -> None:
    contract, snapshot = _world()
    operator_id = "expansion.generic.step"
    plan = ActionIntentPlan.model_validate({
        "goal": "repeat a bounded operation",
        "steps": [
            _step("first", operator_id, "expansion.generic.first"),
            _step("second", operator_id, "expansion.generic.second"),
        ],
    })

    with pytest.raises(ValueError, match="unique operator IDs"):
        ActionIntentPolicyValidator().assess(
            plan,
            contract,
            snapshot,
            operators=(_operator(operator_id, "expansion.generic.first"),),
            task_methods=(),
        )


def test_duplicate_authored_operator_is_rejected_before_dict_collapse() -> None:
    contract, snapshot = _world()
    operator_id = "expansion.generic.step"
    path = "expansion.generic.result"
    plan = ActionIntentPlan.model_validate({
        "goal": "perform one bounded operation",
        "steps": [_step("step", operator_id, path)],
    })
    operator = _operator(operator_id, path)

    with pytest.raises(ValueError, match="proposed operators must have unique IDs"):
        ActionIntentPolicyValidator().assess(
            plan,
            contract,
            snapshot,
            operators=(operator, operator),
            task_methods=(),
        )


def test_prior_step_requirement_cannot_depend_on_failure_only_effect() -> None:
    contract, snapshot = _world()
    produced_ref = "expansion.generic.failed"
    producer = _step("prepare", "expansion.generic.prepare", produced_ref)
    producer["effects"][0].update({
        "role": "consequence",
        "applies_on": "failure",
    })
    consumer = _step(
        "execute",
        "expansion.generic.execute",
        "expansion.generic.completed",
        depends_on=["prepare"],
        requirements=[{
            "requirement_id": "prepared",
            "category": "fact",
            "description": "a usable preparation result",
            "source": "prior_step",
            "producer_step_id": "prepare",
            "produced_ref": produced_ref,
        }],
    )
    plan = ActionIntentPlan.model_validate({
        "goal": "complete a dependent procedure",
        "steps": [producer, consumer],
    })

    with pytest.raises(ValueError, match="failure-only prior-step effect"):
        ActionIntentPolicyValidator().assess_requirements(plan, contract, snapshot)


def test_pushed_failure_effect_requires_a_pushable_check_choice() -> None:
    contract, snapshot = _world()
    operator_id = "expansion.generic.step"
    success_ref = "expansion.generic.succeeded"
    pushed_ref = "expansion.generic.pushed_failed"
    step = _step("step", operator_id, success_ref)
    step["effects"].append({
        "effect_id": "pushed_failure",
        "role": "consequence",
        "applies_on": "pushed_failure",
        "command_kind": "set_fact",
        "target_ref": pushed_ref,
        "value": True,
        "description": "the declared severe consequence",
    })
    plan = ActionIntentPlan.model_validate({
        "goal": "attempt a pushable action",
        "steps": [step],
    })
    operator = ActionOperator.model_validate({
        "operator_id": operator_id,
        "title": "Generic checked step",
        "policy": "required_check",
        "skill_choices": [{
            "skill_key": "generic.skill",
            "reason": "Resolve the declared method.",
            "allow_push": False,
        }],
        "success_commands": [{
            "kind": "set_fact",
            "path": success_ref,
            "value": True,
        }],
        "outcome_branches": [{
            "outcome_key": "pushed_failure",
            "commands": [{
                "kind": "set_fact",
                "path": pushed_ref,
                "value": True,
            }],
        }],
    })

    with pytest.raises(ValueError, match="without an actually pushable skill"):
        ActionIntentPolicyValidator().assess(
            plan, contract, snapshot, operators=(operator,), task_methods=()
        )

    pushable = operator.model_copy(update={
        "skill_choices": (
            operator.skill_choices[0].model_copy(update={"allow_push": True}),
        ),
    })
    assessment = ActionIntentPolicyValidator().assess(
        plan, contract, snapshot, operators=(pushable,), task_methods=()
    )
    assert assessment.status == "executable"
