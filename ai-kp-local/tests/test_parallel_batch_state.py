from __future__ import annotations

import pytest

from ai_kp.platform.resolution.parallel_batch_state import (
    ParallelBatchState,
    ParallelBatchStateMachine,
)


def test_parallel_batch_lifecycle_requires_confirmation_before_commit() -> None:
    machine = ParallelBatchStateMachine()
    initial = ParallelBatchState(status="awaiting_confirmation", version=1)

    with pytest.raises(ValueError, match="Cannot begin_commit"):
        machine.transition(initial, "begin_commit")

    checks = machine.transition(
        initial, "confirmations_completed_with_checks"
    ).current
    ready = machine.transition(checks, "checks_completed").current
    committing = machine.transition(ready, "begin_commit").current
    settled = machine.transition(committing, "commit_succeeded").current

    assert [checks.status, ready.status, committing.status, settled.status] == [
        "awaiting_checks",
        "ready",
        "committing",
        "settled",
    ]
    assert settled.version == 5
    with pytest.raises(ValueError, match="Cannot request_attention"):
        machine.transition(settled, "request_attention")


def test_parallel_batch_can_pause_and_resume_only_to_an_explicit_phase() -> None:
    machine = ParallelBatchStateMachine()
    ready = machine.transition(
        ParallelBatchState(status="awaiting_confirmation", version=1),
        "confirmations_completed_without_checks",
    ).current
    attention = machine.transition(ready, "request_attention").current

    assert attention.status == "needs_attention"
    assert machine.transition(attention, "resume_ready").current.status == "ready"
    assert (
        machine.transition(attention, "resume_confirmations").current.status
        == "awaiting_confirmation"
    )
    assert machine.transition(attention, "resume_checks").current.status == (
        "awaiting_checks"
    )


def test_parallel_batch_terminal_states_reject_all_shortcuts() -> None:
    machine = ParallelBatchStateMachine()

    for status in ("settled", "superseded"):
        with pytest.raises(ValueError, match="Cannot resume_ready"):
            machine.transition(
                ParallelBatchState(status=status, version=9),  # type: ignore[arg-type]
                "resume_ready",
            )
