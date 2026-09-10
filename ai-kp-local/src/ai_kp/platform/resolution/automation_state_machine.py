"""Pure lifecycle transitions for durable Auto KP background work."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

AutomationJobStatus = Literal[
    "queued",
    "running",
    "retry_wait",
    "succeeded",
    "failed",
    "needs_attention",
    "cancelled",
]
AutomationJobEvent = Literal[
    "claim",
    "succeed",
    "request_attention",
    "fail",
    "retry",
    "cancel",
    "recover_stale",
]


class AutomationJobState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AutomationJobStatus
    stage: str = Field(min_length=1, max_length=120)
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1, le=10)


class AutomationTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event: AutomationJobEvent
    previous: AutomationJobState
    current: AutomationJobState
    retry_after_seconds: int | None = Field(default=None, ge=0, le=300)


class AutomationJobStateMachine:
    """Reject illegal transitions and compute bounded exponential retry delay."""

    _ALLOWED: ClassVar[
        dict[AutomationJobEvent, frozenset[AutomationJobStatus]]
    ] = {
        "claim": frozenset({"queued", "retry_wait"}),
        "succeed": frozenset({"running"}),
        "request_attention": frozenset({"running"}),
        "fail": frozenset({"running"}),
        "retry": frozenset({"failed", "needs_attention", "retry_wait"}),
        "cancel": frozenset({"queued", "retry_wait"}),
        "recover_stale": frozenset({"running"}),
    }

    def transition(
        self, state: AutomationJobState, event: AutomationJobEvent
    ) -> AutomationTransition:
        if state.status not in self._ALLOWED[event]:
            raise ValueError(f"Cannot {event} Auto KP job from {state.status}")

        retry_after: int | None = None
        attempt_count = state.attempt_count
        if event == "claim":
            status: AutomationJobStatus = "running"
            stage = "running"
            attempt_count += 1
        elif event == "succeed":
            status, stage = "succeeded", "completed"
        elif event == "request_attention":
            status, stage = "needs_attention", "needs_attention"
        elif event in {"fail", "recover_stale"}:
            if attempt_count < state.max_attempts:
                status, stage = "retry_wait", (
                    "recovered" if event == "recover_stale" else "waiting_retry"
                )
                retry_after = self.retry_delay(attempt_count)
            else:
                status, stage = "failed", "failed"
        elif event == "retry":
            status, stage, attempt_count = "queued", "queued", 0
        else:
            status, stage = "cancelled", "superseded"

        current = AutomationJobState(
            status=status,
            stage=stage,
            attempt_count=attempt_count,
            max_attempts=state.max_attempts,
        )
        return AutomationTransition(
            event=event,
            previous=state,
            current=current,
            retry_after_seconds=retry_after,
        )

    @staticmethod
    def retry_delay(attempt_count: int) -> int:
        if attempt_count < 1:
            raise ValueError("A failed attempt must have been claimed")
        return min(300, 5 * (2 ** (attempt_count - 1)))


__all__ = [
    "AutomationJobState",
    "AutomationJobStateMachine",
    "AutomationTransition",
]
