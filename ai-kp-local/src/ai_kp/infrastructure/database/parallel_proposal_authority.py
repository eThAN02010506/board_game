"""Write-time authority for proposals owned by a parallel action batch."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any, Literal

from ai_kp.infrastructure.database.rows import decode_json_field

ParallelProposalApprovalPhase = Literal["check_request", "commit"]
_TERMINAL_BATCH_STATUSES = frozenset({"settled", "superseded"})


class ParallelProposalAuthority:
    """Prevent generic proposal decisions from bypassing atomic batch state.

    Normal proposal APIs have no batch authority and therefore fail whenever a
    proposal is bound to an unsettled batch.  The parallel workflow gets one
    narrow approval seam whose phase, batch, proposal, action, and adjudication
    state are all reconstructed from durable rows under the caller's write lock.
    """

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def require_legacy_mutation_allowed(self, proposal_id: str) -> None:
        for action_id in self._proposal_action_ids(proposal_id):
            batch = self._batch_for_action(action_id)
            if batch is not None and batch["status"] not in _TERMINAL_BATCH_STATUSES:
                raise ValueError(
                    "A proposal in an active parallel batch can be decided only "
                    "by the atomic parallel workflow"
                )

    def validate_parallel_approval(
        self,
        proposal_id: str,
        *,
        batch_id: str,
        phase: ParallelProposalApprovalPhase,
    ) -> None:
        batch = self._batch(batch_id)
        expected_status = (
            "awaiting_confirmation" if phase == "check_request" else "committing"
        )
        if batch["status"] != expected_status:
            raise ValueError(
                f"Parallel {phase} approval requires a {expected_status} batch"
            )
        proposal = self._proposal(proposal_id)
        if (
            proposal["campaign_id"] != batch["campaign_id"]
            or proposal["status"] != "draft"
        ):
            raise ValueError(
                "Parallel proposal approval requires a draft in the batch campaign"
            )

        direct_item = self._item_for_origin_proposal(batch_id, proposal_id)
        if phase == "check_request":
            self._require_item_mode(direct_item, "skill_check", phase=phase)
            return
        if direct_item is not None:
            self._require_item_mode(direct_item, "direct_resolution", phase=phase)
            return

        basis = self._check_consequence_basis(proposal_id)
        if basis is None:
            raise ValueError(
                "Parallel commit approval requires a bound origin or consequence"
            )
        item = self._item_for_action(batch_id, str(basis.get("player_action_id") or ""))
        self._require_item_mode(item, "skill_check", phase=phase)
        if item is None or basis.get("origin_proposal_id") != item["proposal_id"]:
            raise ValueError(
                "Parallel consequence does not match the batch item's origin proposal"
            )

    def _proposal_action_ids(self, proposal_id: str) -> tuple[str, ...]:
        rows = self.connection.execute(
            "SELECT id FROM player_actions WHERE proposal_id = ? ORDER BY id",
            (proposal_id,),
        ).fetchall()
        action_ids = [str(row["id"]) for row in rows]
        basis = self._check_consequence_basis(proposal_id)
        if basis is not None and basis.get("player_action_id"):
            action_ids.append(str(basis["player_action_id"]))
        return tuple(dict.fromkeys(action_ids))

    def _batch_for_action(self, action_id: str) -> Mapping[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT b.id, b.campaign_id, b.session_id, b.status
            FROM parallel_action_batch_items i
            JOIN parallel_action_batches b ON b.id = i.batch_id
            WHERE i.action_id = ?
            ORDER BY b.created_at DESC, b.id DESC
            LIMIT 1
            """,
            (action_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def _batch(self, batch_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT id, campaign_id, session_id, status
            FROM parallel_action_batches WHERE id = ?
            """,
            (batch_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Parallel batch not found: {batch_id}")
        return dict(row)

    def _proposal(self, proposal_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT id, campaign_id, status FROM turn_proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Turn proposal not found: {proposal_id}")
        return dict(row)

    def _item_for_origin_proposal(
        self,
        batch_id: str,
        proposal_id: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT i.action_id, i.proposal_id, i.outcome_key,
                   a.status AS action_status,
                   j.mode AS adjudication_mode, j.status AS adjudication_status
            FROM parallel_action_batch_items i
            JOIN player_actions a ON a.id = i.action_id
            JOIN player_action_adjudications j ON j.id = i.adjudication_id
            WHERE i.batch_id = ? AND i.proposal_id = ?
            """,
            (batch_id, proposal_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def _item_for_action(
        self,
        batch_id: str,
        action_id: str,
    ) -> dict[str, Any] | None:
        if not action_id:
            return None
        row = self.connection.execute(
            """
            SELECT i.action_id, i.proposal_id, i.outcome_key,
                   a.status AS action_status,
                   j.mode AS adjudication_mode, j.status AS adjudication_status
            FROM parallel_action_batch_items i
            JOIN player_actions a ON a.id = i.action_id
            JOIN player_action_adjudications j ON j.id = i.adjudication_id
            WHERE i.batch_id = ? AND i.action_id = ?
            """,
            (batch_id, action_id),
        ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _require_item_mode(
        item: Mapping[str, Any] | None,
        expected_mode: str,
        *,
        phase: ParallelProposalApprovalPhase,
    ) -> None:
        if (
            item is None
            or item.get("action_status") != "reviewed"
            or item.get("adjudication_mode") != expected_mode
            or item.get("adjudication_status") != "confirmed"
            or (phase == "commit" and item.get("outcome_key") is None)
        ):
            raise ValueError(
                "Parallel proposal approval lost its confirmed item authority"
            )

    def _check_consequence_basis(self, proposal_id: str) -> dict[str, Any] | None:
        rows = self.connection.execute(
            """
            SELECT payload_json FROM proposal_actions
            WHERE proposal_id = ? AND action_type = 'check_consequence_basis'
            ORDER BY created_at, id
            """,
            (proposal_id,),
        ).fetchall()
        if len(rows) > 1:
            raise ValueError("A proposal has multiple check consequence bases")
        if not rows:
            return None
        payload = decode_json_field(rows[0]["payload_json"], {})
        if not isinstance(payload, dict):
            raise TypeError("Check consequence basis must be an object")
        return payload


__all__ = ["ParallelProposalApprovalPhase", "ParallelProposalAuthority"]
