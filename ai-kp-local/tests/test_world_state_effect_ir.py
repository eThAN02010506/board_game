import pytest

from ai_kp.platform.resolution.contracts import WorldCommand
from ai_kp.platform.resolution.scenario_ir import ScenarioIrAssembler
from ai_kp.platform.resolution.scenario_ir_models import IrAction, IrEntity
from ai_kp.platform.resolution.world_state_effect_ir import (
    IrWorldStateEffect,
    lower_world_state_effects,
)
from tests.test_scenario_ir import batch, source_ref


def effect(**changes):
    return IrWorldStateEffect.model_validate(
        {
            "entity_id": "log",
            "dimension": "condition",
            "value": "read",
            "visibility": "kp",
            "applies_on": "success",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "outcome,field",
    [
        ("always", "always"),
        ("success", "on_success"),
        ("failure", "on_failure"),
        ("pushed_failure", "on_pushed_failure"),
    ],
)
def test_state_record_lowers_to_exact_branch_without_mutating_input(outcome, field):
    original = IrAction(
        id="read-log",
        title="Read",
        policy="automatic",
        global_action=True,
        world_state_effects=(effect(applies_on=outcome),),
        source_block_ids=("source",),
    )
    lowered = lower_world_state_effects(original)
    assert getattr(lowered, field) == (effect(applies_on=outcome).to_command(),)
    assert lowered.world_state_effects == ()
    assert original.world_state_effects
    assert lower_world_state_effects(lowered) is lowered
    assert IrAction.model_validate_json(lowered.model_dump_json()) == lowered


@pytest.mark.parametrize("value", [1.5, [], {}, " ", 10**9 + 1])
def test_invalid_state_values_are_not_coerced(value):
    with pytest.raises(ValueError):
        effect(value=value)


def test_lowering_preserves_boolean_integer_distinction_and_rejects_budget_overflow():
    action = IrAction(
        id="read",
        title="Read",
        policy="automatic",
        global_action=True,
        on_success=(effect(value=True).to_command(),),
        world_state_effects=(effect(value=1),),
        source_block_ids=("source",),
    )
    assert len(lower_world_state_effects(action).on_success) == 2
    full = action.model_copy(
        update={
            "on_success": tuple(
                WorldCommand(kind="set_fact", path=f"fact_{index}", value=True)
                for index in range(3)
            )
        }
    )
    with pytest.raises(ValueError, match="command budget"):
        lower_world_state_effects(full)


def test_assembly_generates_world_state_command_from_compact_record():
    original = batch("source")
    action = original.actions[0].model_copy(
        update={
            "on_success": (),
            "world_state_effects": (effect(),),
        }
    )
    ir = original.model_copy(
        update={
            "actions": (action,),
            "entities": (
                IrEntity(
                    id="log",
                    type="item",
                    title="Log",
                    module_entity_id="module-log",
                    source_block_ids=("source",),
                ),
            ),
        }
    )
    result = ScenarioIrAssembler().assemble(
        (ir,),
        source_refs={"source": source_ref("source")},
        contract_id="state-ir",
        source_version=1,
        ruleset_id="coc7",
        title="State IR",
        corpus_truncated=False,
    )
    operator = next(item for item in result.contract.operators if item.operator_id == action.id)
    assert operator.success_commands == (effect().to_command(),)
    assert result.contract.initial_facts == {}
