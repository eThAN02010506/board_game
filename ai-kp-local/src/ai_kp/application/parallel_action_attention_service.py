"""Trusted recovery operations for durable parallel-action batches."""

from __future__ import annotations

from typing import Any

from ai_kp.application.parallel_action_workflow_authority import (
    ParallelActionWorkflowAuthority,
)
from ai_kp.application.parallel_action_workflow_models import ParallelWorkflowResult
from ai_kp.platform.sessions.models import AuthenticatedMember


class ParallelActionAttentionService:
    """Resume or abandon an audited batch without changing resolved dice.

    Keeping recovery separate from normal consent/check/commit orchestration makes
    the exceptional coordinator path easier to audit.  The repository still owns
    every transaction and state-machine transition.
    """

    def __init__(
        self,
        repo: Any,
        *,
        authority: ParallelActionWorkflowAuthority | None = None,
    ):
        self.repo = repo
        self.authority = authority or ParallelActionWorkflowAuthority(repo)

    def resume(
        self,
        batch_id: str,
        *,
        expected_version: int,
        identity: AuthenticatedMember,
        reason: str,
    ) -> ParallelWorkflowResult:
        """Resume only the barrier proven by current durable authority."""

        self.authority.require_kp(identity)
        self.repo.begin_parallel_action_workflow()
        try:
            batch = self.repo.get_parallel_action_batch(batch_id)
            self.authority.require_active_kp_for_batch(identity, batch)
            if batch["status"] == "settled":
                batch = self.repo.validate_parallel_action_batch_settlement(batch_id)
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    "settled", batch, "Parallel batch is already settled."
                )
            if self._is_resume_replay(
                batch,
                expected_version=expected_version,
                actor_member_id=identity.member_id,
            ):
                batch = self.repo.validate_parallel_action_batch_authority(batch_id)
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    self.authority.workflow_status(batch),
                    batch,
                    "Replayed the existing audited parallel-batch recovery.",
                )
            if batch["status"] != "needs_attention":
                raise ValueError(
                    "Only a needs-attention parallel batch can be resumed"
                )
            if int(batch["version"]) != expected_version:
                raise ValueError(
                    "Parallel action batch changed; refresh before recovery"
                )
            batch = self.repo.validate_parallel_action_batch_authority(batch_id)
            event, status, barrier_message = self.authority.recovery_event(batch)
            batch = self.repo.transition_parallel_action_batch(
                batch_id,
                event,
                expected_version=expected_version,
                actor_member_id=identity.member_id,
                reason=reason,
            )
            self.repo.finish_parallel_action_workflow()
        except Exception:
            self.repo.rollback_parallel_action_workflow()
            raise
        return self.authority.result(status, batch, barrier_message)

    def abandon(
        self,
        batch_id: str,
        *,
        expected_version: int,
        identity: AuthenticatedMember,
        reason: str,
    ) -> ParallelWorkflowResult:
        """Audit and terminally dispose an attention batch so players can replan."""

        self.authority.require_kp(identity)
        self.repo.begin_parallel_action_workflow()
        try:
            batch = self.repo.get_parallel_action_batch(batch_id)
            self.authority.require_active_kp_for_batch(identity, batch)
            if batch["status"] == "settled":
                self.repo.validate_parallel_action_batch_settlement(batch_id)
                raise ValueError("A settled parallel batch cannot be abandoned")
            if batch["status"] == "superseded":
                self.authority.require_complete_superseded_disposition(batch)
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    "needs_attention",
                    batch,
                    "Replayed the existing audited parallel-batch abandonment.",
                )
            if batch["status"] != "needs_attention":
                raise ValueError(
                    "Only a needs-attention parallel batch can be abandoned"
                )
            if int(batch["version"]) != expected_version:
                raise ValueError(
                    "Parallel action batch changed; refresh before abandonment"
                )
            abandonment = self.repo.get_parallel_action_abandonment_decision(batch_id)
            if not abandonment["abandon_allowed"]:
                raise ValueError(str(abandonment["abandon_block_reason"]))
            batch = self.repo.transition_parallel_action_batch(
                batch_id,
                "supersede",
                expected_version=expected_version,
                actor_member_id=identity.member_id,
                reason=reason,
            )
            batch = self.repo.dispose_superseded_parallel_actions(
                batch_id,
                actor_member_id=identity.member_id,
                reason=reason,
            )
            self.repo.finish_parallel_action_workflow()
        except Exception:
            self.repo.rollback_parallel_action_workflow()
            raise
        return self.authority.result(
            "needs_attention",
            batch,
            "The abandoned batch is terminal; players may submit a fresh batch.",
        )

    @staticmethod
    def _is_resume_replay(
        batch: dict[str, Any],
        *,
        expected_version: int,
        actor_member_id: str,
    ) -> bool:
        event_for_status = {
            "awaiting_confirmation": "resume_confirmations",
            "awaiting_checks": "resume_checks",
            "ready": "resume_ready",
        }
        expected_event = event_for_status.get(str(batch["status"]))
        if expected_event is None or int(batch["version"]) != expected_version + 1:
            return False
        events = batch.get("events") or ()
        if not events:
            return False
        event = events[-1]
        return (
            event.get("event_type") == expected_event
            and event.get("actor_member_id") == actor_member_id
            and int(event.get("version") or -1) == int(batch["version"])
        )


__all__ = ["ParallelActionAttentionService"]
