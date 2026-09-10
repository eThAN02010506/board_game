from __future__ import annotations

import pytest

from ai_kp.platform.resolution.contracts import ActionIntent
from ai_kp.platform.resolution.parallel import (
    ParallelActionKernel,
    ParallelIntent,
    ParallelSettlementRequest,
)
from ai_kp.platform.resolution.settlement import resolved_action_commands
from tests.test_parallel_action_kernel import parallel_contract


def test_single_and_parallel_settlement_share_resolved_action_event_shape() -> None:
    contract = parallel_contract()
    snapshot = contract.initial_snapshot("shared-settlement")
    request = ParallelSettlementRequest(
        batch_id="shared-event-batch",
        intents=(
            ParallelIntent(
                action_id="a",
                actor_id="pc-a",
                operator_id="observe-quietly",
                outcome="success",
            ),
            ParallelIntent(
                action_id="b",
                actor_id="pc-b",
                operator_id="find-mark",
                outcome="success",
            ),
        ),
    )
    parallel = ParallelActionKernel(contract).preview(snapshot, request)
    action = next(item for item in parallel.actions if item.action_id == "a")
    expected = resolved_action_commands(
        action.preview,
        "success",
        actor_id="pc-a",
    )[0]
    actual = next(
        command
        for command in parallel.commands
        if command.kind == "emit_event"
        and command.payload.get("action_id") == "a"
    )

    assert actual == expected
    assert actual.payload == {
        "action_id": "a",
        "operator_id": "observe-quietly",
        "outcome": "success",
        "actor_id": "pc-a",
    }


def test_resolved_action_projection_rejects_unknown_outcome_before_event() -> None:
    contract = parallel_contract()
    snapshot = contract.initial_snapshot("shared-settlement-invalid")
    preview = ParallelActionKernel(contract).kernel.preview(
        snapshot,
        ActionIntent(
            action_id="a",
            actor_id="pc-a",
            operator_id="observe-quietly",
            goal="observe",
        ),
    )

    with pytest.raises(ValueError, match="Unsupported kernel outcome"):
        resolved_action_commands(preview, "invented", actor_id="pc-a")
