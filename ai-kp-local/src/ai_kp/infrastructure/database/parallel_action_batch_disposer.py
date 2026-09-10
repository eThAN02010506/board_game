"""Atomic cleanup for a superseded parallel-action batch."""

from __future__ import annotations

import sqlite3
from typing import Any, Protocol


class _ParallelBatchDispositionHost(Protocol):
    """Only the composed repository capabilities needed during disposition."""

    connection: sqlite3.Connection

    def get_parallel_action_batch(self, batch_id: str) -> dict[str, Any]: ...

    def get_turn_proposal(self, proposal_id: str) -> dict[str, Any]: ...

    def reject_turn_proposal(
        self,
        proposal_id: str,
        actor: str = "human_kp",
        note: str = "",
    ) -> dict[str, Any]: ...

    def resolve_player_action_for_proposal(
        self,
        proposal_id: str,
        status: str,
    ) -> None: ...

    def get_action_adjudication(self, action_id: str) -> dict[str, Any]: ...

    def _add_check_action(
        self,
        check_id: str,
        action_type: str,
        actor_member_id: str,
        *,
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None: ...

    def _append_check_realtime(self, check_id: str, event_type: str) -> None: ...

    def _append_adjudication_event(
        self,
        adjudication_id: str,
        version: int,
        event_type: str,
        actor_member_id: str | None,
        payload: dict[str, Any],
    ) -> None: ...


class ParallelActionBatchDisposer:
    """Retire the pending surfaces of one already-superseded batch.

    The caller owns the transaction lifecycle.  The explicit connection keeps
    direct SQL and composed repository calls on the same SQLite transaction.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        host: _ParallelBatchDispositionHost,
    ) -> None:
        if host.connection is not connection:
            raise ValueError("Parallel disposition requires one shared connection")
        self.connection = connection
        self.host = host

    def dispose(
        self,
        batch_id: str,
        *,
        actor_member_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Retire every pending surface while preserving resolved-roll audit."""

        if not self.connection.in_transaction:
            raise RuntimeError("Parallel disposition requires an outer transaction")
        batch = self.host.get_parallel_action_batch(batch_id)
        if batch["status"] != "superseded":
            raise ValueError("Only a superseded parallel batch can be disposed")
        normalized_reason = reason.strip() or "Parallel player consent was withdrawn."
        for item in batch["items"]:
            # Cancel unresolved rolls before rejecting their actions.  This is a
            # batch withdrawal, not a terminal-result notification, so do not
            # enqueue a consequence for the cancelled lineage.
            for check in item.get("checks") or ():
                if check["status"] != "requested":
                    continue
                updated_check = self.connection.execute(
                    """
                    UPDATE skill_checks
                    SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'requested'
                    """,
                    (check["id"],),
                )
                if updated_check.rowcount != 1:
                    raise ValueError(
                        "Parallel check changed during batch supersession"
                    )
                self.host._add_check_action(
                    str(check["id"]),
                    "cancelled",
                    actor_member_id,
                    reason=normalized_reason[:1000],
                )
                self.host._append_check_realtime(
                    str(check["id"]), "check.cancelled"
                )
            proposal = self.host.get_turn_proposal(str(item["proposal_id"]))
            if proposal["status"] == "draft":
                self.host.reject_turn_proposal(
                    str(proposal["id"]),
                    actor="system",
                    note=normalized_reason[:1000],
                )
            else:
                # Approved check origins remain immutable evidence; only their
                # unresolved player action is retired.
                self.host.resolve_player_action_for_proposal(
                    str(proposal["id"]), "rejected"
                )
            adjudication = self.host.get_action_adjudication(
                str(item["action_id"])
            )
            if adjudication["status"] != "superseded":
                updated = self.connection.execute(
                    """
                    UPDATE player_action_adjudications
                    SET status = 'superseded', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status IN ('pending', 'confirmed')
                    """,
                    (adjudication["id"],),
                )
                if updated.rowcount != 1:
                    raise ValueError(
                        "Parallel adjudication changed during supersession"
                    )
                self.host._append_adjudication_event(
                    str(adjudication["id"]),
                    int(adjudication["version"]),
                    "superseded",
                    actor_member_id,
                    {
                        "parallel_batch_id": batch_id,
                        "reason": normalized_reason[:1000],
                    },
                )
        return self.host.get_parallel_action_batch(batch_id)


__all__ = ["ParallelActionBatchDisposer"]
