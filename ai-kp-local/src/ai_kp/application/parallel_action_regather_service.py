"""Deterministic coordination agent for revised simultaneous actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_kp.platform.sessions.models import AuthenticatedMember


@dataclass(frozen=True)
class ParallelActionRegatherProjection:
    id: str
    status: str
    version: int
    participant_count: int
    submitted_count: int
    waiting_count: int
    self_phase: str
    own_action_id: str | None
    updated_at: str
    public_message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "version": self.version,
            "participant_count": self.participant_count,
            "submitted_count": self.submitted_count,
            "waiting_count": self.waiting_count,
            "self_phase": self.self_phase,
            "own_action_id": self.own_action_id,
            "updated_at": self.updated_at,
            "public_message": self.public_message,
        }


class ParallelActionRegatherCoordinator:
    """Own the durable consent barrier, never model or rules decisions.

    The coordinator is intentionally a narrow deterministic agent.  It hands
    the exact replacement actions back to the existing model-agent planning
    pipeline only after every original participant explicitly opts in.
    """

    def __init__(self, repo: Any):
        self.repo = repo

    def start(
        self,
        source_batch_id: str,
        *,
        identity: AuthenticatedMember,
        reason: str,
    ) -> dict[str, Any]:
        self._require_player(identity)
        batch = self.repo.get_parallel_action_batch(source_batch_id)
        self._require_scope(batch, identity)
        return self.repo.create_parallel_action_regather(
            source_batch_id,
            actor_member_id=identity.member_id,
            reason=reason,
        )

    def register(
        self,
        regather_id: str,
        action_id: str,
        *,
        expected_version: int,
        identity: AuthenticatedMember,
    ) -> dict[str, Any]:
        self._require_player(identity)
        regather = self.repo.get_parallel_action_regather(regather_id)
        self._require_scope(regather, identity)
        try:
            return self.repo.register_parallel_action_regather_submission(
                regather_id,
                action_id,
                actor_member_id=identity.member_id,
                expected_version=expected_version,
                auto_kp_requested=True,
            )
        except ValueError as exc:
            if "changed; refresh" not in str(exc):
                raise
            # Another participant may have crossed this request between its
            # read and write. Re-read once under the repository's serialized
            # boundary; never retry a conflicting submission from this member.
            current = self.repo.get_parallel_action_regather(regather_id)
            return self.repo.register_parallel_action_regather_submission(
                regather_id,
                action_id,
                actor_member_id=identity.member_id,
                expected_version=int(current["version"]),
                auto_kp_requested=True,
            )

    def current_projection(
        self, identity: AuthenticatedMember
    ) -> ParallelActionRegatherProjection | None:
        self._require_player(identity)
        regather = self.repo.get_active_parallel_action_regather_for_member(
            identity.campaign_id,
            identity.session_id,
            identity.member_id,
        )
        return self.project(regather, identity) if regather is not None else None

    def project(
        self,
        regather: dict[str, Any],
        identity: AuthenticatedMember,
    ) -> ParallelActionRegatherProjection:
        self._require_scope(regather, identity)
        own = next(
            (
                item
                for item in regather["members"]
                if str(item["member_id"]) == identity.member_id
            ),
            None,
        )
        if own is None:
            raise PermissionError("Member does not participate in this regather")
        own_action_id = (
            str(own["replacement_action_id"])
            if own.get("replacement_action_id")
            else None
        )
        status = str(regather["status"])
        self_phase = (
            "processing"
            if status == "queued"
            else "waiting_for_others"
            if own_action_id is not None
            else "awaiting_submission"
        )
        participant_count = int(regather["participant_count"])
        submitted_count = int(regather["submitted_count"])
        waiting_count = max(0, participant_count - submitted_count)
        message = (
            "All revised actions are queued for the existing parallel planner."
            if status == "queued"
            else f"Waiting for {waiting_count} participant(s) to resubmit."
            if own_action_id is not None
            else "The group revised its plan. Submit your current action when ready."
        )
        return ParallelActionRegatherProjection(
            id=str(regather["id"]),
            status=status,
            version=int(regather["version"]),
            participant_count=participant_count,
            submitted_count=submitted_count,
            waiting_count=waiting_count,
            self_phase=self_phase,
            own_action_id=own_action_id,
            updated_at=str(regather["updated_at"]),
            public_message=message,
        )

    @staticmethod
    def _require_player(identity: AuthenticatedMember) -> None:
        if identity.role != "player":
            raise PermissionError("Parallel regrouping is a player consent workflow")

    @staticmethod
    def _require_scope(value: dict[str, Any], identity: AuthenticatedMember) -> None:
        if (
            str(value["campaign_id"]) != identity.campaign_id
            or str(value["session_id"]) != identity.session_id
        ):
            raise PermissionError("Parallel regather is outside member scope")


__all__ = [
    "ParallelActionRegatherCoordinator",
    "ParallelActionRegatherProjection",
]
