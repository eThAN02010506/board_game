import pytest

from ai_kp.platform.resolution.contracts import WorldCommand
from ai_kp.platform.resolution.parallel import ParallelActionKernel, ParallelSettlementRequest
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from tests.test_parallel_action_kernel import intent, parallel_contract


def runtime(**payload):
    return WorldCommand(kind="update_entity_runtime", entity_id="key", payload=payload)


@pytest.mark.parametrize(
    ("left", "right", "conflict"),
    [
        (runtime(attitude="friendly"), runtime(attitude="hostile"), True),
        (runtime(attitude="friendly"), runtime(physical_state="injured"), False),
        (runtime(attitude="friendly"), runtime(attitude="friendly"), False),
        (runtime(add_memory_ref="a"), runtime(add_memory_ref="b"), False),
        (
            WorldCommand(kind="set_fact", path="x", value=True),
            WorldCommand(kind="set_fact", path="x", value=1),
            True,
        ),
        (
            WorldCommand(kind="set_fact", path="x", value="__removed__"),
            WorldCommand(kind="remove_fact", path="x"),
            True,
        ),
        (
            WorldCommand(kind="set_fact", path="x", value=None),
            WorldCommand(kind="remove_fact", path="x"),
            True,
        ),
    ],
)
def test_compiler_and_parallel_share_assignment_semantics(left, right, conflict):
    contract = parallel_contract()
    operators = (
        contract.operators[1].model_copy(update={"success_commands": (left,)}),
        contract.operators[2].model_copy(update={"success_commands": (right,)}),
    )
    parallel = contract.model_copy(update={"operators": operators})
    snapshot = parallel.initial_snapshot("run-writes")
    result = ParallelActionKernel(parallel).preview(
        snapshot,
        ParallelSettlementRequest(
            batch_id="writes",
            intents=(
                intent("a", "pc-a", operators[0].operator_id),
                intent("b", "pc-b", operators[1].operator_id),
            ),
        ),
    )
    assert result.status == ("conflict" if conflict else "ready")
    if conflict:
        assert result.commands == ()
        assert result.resulting_snapshot == snapshot
    elif "add_memory_ref" in left.payload:
        assert result.resulting_snapshot.entity_runtime["key"].memory_refs == ("a", "b")
    else:
        for field, value in {**left.payload, **right.payload}.items():
            assert getattr(result.resulting_snapshot.entity_runtime["key"], field) == value

    single = contract.model_copy(
        update={"operators": (operators[0].model_copy(update={"success_commands": (left, right)}),)}
    )
    issues = tuple(ScenarioContractCompiler()._command_conflicts(single))
    assert bool(issues) is conflict
