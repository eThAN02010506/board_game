"""Explicit lifecycle for durable parallel-action coordination."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

ParallelBatchStatus = Literal[
    "awaiting_confirmation",
    "awaiting_checks",
    "ready",
    "committing",
    "settled",
    "needs_attention",
    "superseded",
]
ParallelBatchEvent = Literal[
    "confirmations_completed_with_checks",
    "confirmations_completed_without_checks",
    "checks_completed",
    "begin_commit",
    "commit_succeeded",
    "request_attention",
    "supersede",
    "resume_confirmations",
    "resume_checks",
    "resume_ready",
]


class ParallelBatchState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ParallelBatchStatus
    version: int = Field(ge=1)


class ParallelBatchTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event: ParallelBatchEvent
    previous: ParallelBatchState
    current: ParallelBatchState


class ParallelBatchStateMachine:
    """Reject lifecycle shortcuts that could bypass consent or check results."""

    _TRANSITIONS: ClassVar[
        dict[tuple[ParallelBatchStatus, ParallelBatchEvent], ParallelBatchStatus]
    ] = {
        (
            "awaiting_confirmation",
            "confirmations_completed_with_checks",
        ): "awaiting_checks",
        (
            "awaiting_confirmation",
            "confirmations_completed_without_checks",
        ): "ready",
        ("awaiting_checks", "checks_completed"): "ready",
        ("ready", "begin_commit"): "committing",
        ("committing", "commit_succeeded"): "settled",
        ("awaiting_confirmation", "request_attention"): "needs_attention",
        ("awaiting_checks", "request_attention"): "needs_attention",
        ("ready", "request_attention"): "needs_attention",
        ("committing", "request_attention"): "needs_attention",
        ("awaiting_confirmation", "supersede"): "superseded",
        ("awaiting_checks", "supersede"): "superseded",
        ("ready", "supersede"): "superseded",
        ("needs_attention", "supersede"): "superseded",
        ("needs_attention", "resume_confirmations"): "awaiting_confirmation",
        ("needs_attention", "resume_checks"): "awaiting_checks",
        ("needs_attention", "resume_ready"): "ready",
    }

    def transition(
        self,
        state: ParallelBatchState,
        event: ParallelBatchEvent,
    ) -> ParallelBatchTransition:
        target = self._TRANSITIONS.get((state.status, event))
        if target is None:
            raise ValueError(
                f"Cannot {event} parallel batch from {state.status}"
            )
        return ParallelBatchTransition(
            event=event,
            previous=state,
            current=ParallelBatchState(status=target, version=state.version + 1),
        )


__all__ = [
    "ParallelBatchEvent",
    "ParallelBatchState",
    "ParallelBatchStateMachine",
    "ParallelBatchStatus",
    "ParallelBatchTransition",
]
