from __future__ import annotations

import pytest

from ai_kp.platform.resolution.automation_state_machine import (
    AutomationJobState,
    AutomationJobStateMachine,
)


def state(status: str, attempt_count: int = 0, max_attempts: int = 3):
    return AutomationJobState.model_validate(
        {
            "status": status,
            "stage": status,
            "attempt_count": attempt_count,
            "max_attempts": max_attempts,
        }
    )


def test_job_lifecycle_has_bounded_deterministic_backoff_and_recovery() -> None:
    machine = AutomationJobStateMachine()

    running = machine.transition(state("queued"), "claim").current
    waiting = machine.transition(running, "fail")
    claimed_again = machine.transition(waiting.current, "claim").current
    recovered = machine.transition(claimed_again, "recover_stale")

    assert running.status == "running"
    assert running.attempt_count == 1
    assert waiting.current.status == "retry_wait"
    assert waiting.retry_after_seconds == 5
    assert recovered.current.status == "retry_wait"
    assert recovered.current.stage == "recovered"
    assert recovered.retry_after_seconds == 10


def test_job_fails_after_attempt_budget_and_manual_retry_resets_budget() -> None:
    machine = AutomationJobStateMachine()
    exhausted = machine.transition(state("running", 3, 3), "fail")
    retried = machine.transition(exhausted.current, "retry")

    assert exhausted.current.status == "failed"
    assert exhausted.retry_after_seconds is None
    assert retried.current.status == "queued"
    assert retried.current.attempt_count == 0


@pytest.mark.parametrize(
    ("status", "event"),
    [("succeeded", "claim"), ("queued", "succeed"), ("running", "cancel")],
)
def test_illegal_job_transitions_are_rejected(status: str, event: str) -> None:
    with pytest.raises(ValueError, match="Cannot"):
        AutomationJobStateMachine().transition(state(status), event)  # type: ignore[arg-type]
