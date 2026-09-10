"""Stable application contracts for the durable parallel-action workflow."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ai_kp.application.parallel_action_planning_service import (
    ParallelPlanningBatch,
    ParallelSkillRepreview,
    UnsupportedParallelAction,
)
from ai_kp.application.ports.director import KernelDirector

ParallelWorkflowStatus = Literal[
    "awaiting_confirmation",
    "awaiting_checks",
    "ready",
    "settled",
    "needs_attention",
]


class ParallelActionPlanner(Protocol):
    async def plan(
        self,
        actions: Sequence[dict[str, Any]],
        director: KernelDirector,
        *,
        source_model: str,
        profile: str = "small",
    ) -> ParallelPlanningBatch: ...

    def repreview_persisted_skill(
        self,
        batch_id: str,
        action_id: str,
        *,
        requested_skill_key: str,
        actor_member_id: str,
    ) -> ParallelSkillRepreview: ...


@dataclass(frozen=True)
class ParallelWorkflowResult:
    """Internal authority-rich result; player APIs must use a scoped projection."""

    status: ParallelWorkflowStatus
    batch: dict[str, Any] | None
    actions: tuple[dict[str, Any], ...] = ()
    checks: tuple[dict[str, Any], ...] = ()
    unsupported: tuple[UnsupportedParallelAction, ...] = ()
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the internal coordinator view, never a player API projection.

        The durable batch can contain other players' rulings and check details.
        Player routes must use a member-scoped projection instead of this helper.
        """

        return {
            "status": self.status,
            "batch": self.batch,
            "actions": list(self.actions),
            "checks": list(self.checks),
            "unsupported": [
                {
                    "action_id": item.action_id,
                    "actor_id": item.actor_id,
                    "route": item.route,
                    "reason": item.reason,
                }
                for item in self.unsupported
            ],
            "message": self.message,
        }


__all__ = [
    "ParallelActionPlanner",
    "ParallelWorkflowResult",
    "ParallelWorkflowStatus",
]
