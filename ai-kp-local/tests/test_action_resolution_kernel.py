from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_kp.platform.resolution.action_kernel import (
    ActionIntent,
    ActionOperator,
    ActionResolutionKernel,
    OutcomeBranch,
    ScenarioSnapshot,
    SkillChoice,
    StateCondition,
    WorldCommand,
)


def snapshot(**updates: object) -> ScenarioSnapshot:
    values: dict[str, object] = {
        "run_id": "run-1",
        "scenario_version": 3,
        "run_version": 7,
        "scene_id": "location-a",
        "facts": {"environment": {"hazard_active": True}},
        "resources": {"cash": 20},
        "clocks": {"threat": 1},
    }
    values.update(updates)
    return ScenarioSnapshot.model_validate(values)


def intent(**updates: object) -> ActionIntent:
    values: dict[str, object] = {
        "action_id": "action-1",
        "actor_id": "pc-1",
        "goal": "进入相邻地点",
        "method": "打开通道",
        "operator_id": "move-adjacent",
    }
    values.update(updates)
    return ActionIntent.model_validate(values)


def test_automatic_preview_is_stable_and_preflight_isolated() -> None:
    operator = ActionOperator(
        operator_id="move-adjacent",
        title="Move to an adjacent location",
        policy="automatic",
        preconditions=(StateCondition(path="scene_id", operator="eq", value="location-a"),),
        success_commands=(WorldCommand(kind="set_scene", value="location-b"),),
        maximum_effect="The actor enters the adjacent location.",
    )
    kernel = ActionResolutionKernel([operator])

    first = kernel.preview(snapshot(), intent())
    second = kernel.preview(snapshot(), intent())

    assert first == second
    assert first.allowed is True
    assert first.preview_hash == "88bcf41a1dccceb3c4342486c5eabc03359c804ae2e3bb699f07d5c368d8f6cc"
    updated = kernel.preflight(snapshot(), first.success_commands)
    assert updated.scene_id == "location-b"
    assert updated.run_version == 8
    assert snapshot().scene_id == "location-a"


def test_missing_precondition_blocks_effects() -> None:
    operator = ActionOperator(
        operator_id="move-adjacent",
        title="Move",
        policy="automatic",
        preconditions=(StateCondition(path="facts.door_open", operator="eq", value=True),),
        success_commands=(WorldCommand(kind="set_scene", value="location-b"),),
    )

    preview = ActionResolutionKernel([operator]).preview(snapshot(), intent())

    assert preview.allowed is False
    assert preview.success_commands == ()
    assert "facts.door_open" in preview.reason


@pytest.mark.parametrize("policy", ["required_check", "optional_check"])
def test_check_policy_keeps_player_changeable_authorized_options(policy: str) -> None:
    operator = ActionOperator(
        operator_id="move-adjacent",
        title="Risky movement",
        policy=policy,  # type: ignore[arg-type]
        skill_choices=(
            SkillChoice(skill_key="jump", reason="Leap over the gap."),
            SkillChoice(skill_key="dex", reason="Keep balance while crossing."),
        ),
    )
    kernel = ActionResolutionKernel([operator])

    default = kernel.preview(snapshot(), intent())
    changed = kernel.preview(snapshot(), intent(requested_skill_key="dex"))

    assert default.policy == policy
    assert default.selected_skill_key == "jump"
    assert changed.selected_skill_key == "dex"
    assert [choice.skill_key for choice in changed.skill_choices] == ["jump", "dex"]


def test_outcome_independent_time_cost_and_exact_success_tier_are_deterministic() -> None:
    operator = ActionOperator(
        operator_id="move-adjacent",
        title="Research while pressure advances",
        policy="required_check",
        skill_choices=(SkillChoice(skill_key="library_use", reason="Research records."),),
        always_commands=(
            WorldCommand(kind="advance_clock", clock_id="threat", delta=1),
        ),
        success_commands=(
            WorldCommand(kind="set_fact", path="clue.summary", value=True),
        ),
        failure_commands=(
            WorldCommand(kind="set_fact", path="research.delayed", value=True),
        ),
        outcome_branches=(
            OutcomeBranch(
                outcome_key="hard",
                commands=(
                    WorldCommand(kind="set_fact", path="clue.summary", value=True),
                    WorldCommand(kind="set_fact", path="clue.detail", value=True),
                ),
            ),
        ),
    )
    preview = ActionResolutionKernel([operator]).preview(snapshot(), intent())
    kernel = ActionResolutionKernel([operator])

    failed = kernel.preflight(snapshot(), preview.commands_for_outcome("failure"))
    hard = kernel.preflight(snapshot(), preview.commands_for_outcome("hard"))

    assert failed.clocks["threat"] == 2
    assert failed.facts["research"]["delayed"] is True
    assert hard.clocks["threat"] == 2
    assert hard.facts["clue"] == {"summary": True, "detail": True}


def test_unknown_skill_condition_and_command_fail_closed() -> None:
    operator = ActionOperator(
        operator_id="move-adjacent",
        title="Risky movement",
        policy="required_check",
        skill_choices=(SkillChoice(skill_key="jump", reason="Leap."),),
    )
    with pytest.raises(ValueError, match="not authorized"):
        ActionResolutionKernel([operator]).preview(
            snapshot(), intent(requested_skill_key="mechanical_repair")
        )
    with pytest.raises(ValidationError):
        StateCondition.model_validate({"path": "scene_id", "operator": "execute", "value": 1})
    with pytest.raises(ValidationError):
        WorldCommand.model_validate({"kind": "sql", "value": "DROP TABLE"})


def test_command_preflight_validates_resources_and_terminal_state() -> None:
    kernel = ActionResolutionKernel([])
    with pytest.raises(ValueError, match="negative"):
        kernel.preflight(
            snapshot(), [WorldCommand(kind="adjust_resource", path="cash", delta=-21)]
        )

    completed = kernel.preflight(
        snapshot(), [WorldCommand(kind="complete_run", value="ending-c")]
    )
    assert completed.status == "completed"
    assert completed.ending_id == "ending-c"
    with pytest.raises(ValueError, match="reject new actions"):
        kernel.preview(completed, intent())
    with pytest.raises(ValueError, match="reject world commands"):
        kernel.preflight(completed, [WorldCommand(kind="emit_event", event_type="late")])
