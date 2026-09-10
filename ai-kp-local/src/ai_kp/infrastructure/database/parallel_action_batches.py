"""Transactional persistence for durable parallel-action batches."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.parallel_action_abandonment import (
    ParallelActionAbandonmentAuthority,
)
from ai_kp.infrastructure.database.parallel_action_batch_authority import (
    ParallelActionBatchAuthority,
)
from ai_kp.infrastructure.database.parallel_action_batch_disposer import (
    ParallelActionBatchDisposer,
)
from ai_kp.infrastructure.database.parallel_action_skill_rebinder import (
    ParallelActionSkillRebinder,
)
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.resolution.parallel_batch_state import (
    ParallelBatchEvent,
    ParallelBatchState,
    ParallelBatchStateMachine,
)

_ACTIVE_STATUSES = (
    "awaiting_confirmation",
    "awaiting_checks",
    "ready",
    "committing",
    "needs_attention",
)


class ParallelActionBatchRepository(SQLiteRepository):
    """Own batch CRUD, transaction boundaries, CAS writes, and audit events."""

    # Repository is composed with other repository classes through multiple
    # inheritance.  Keep domain state machines under unique names so MRO lookup
    # cannot make, for example, an Auto KP job interpret ``claim`` as a parallel
    # batch transition.
    _parallel_batch_state_machine = ParallelBatchStateMachine()

    def begin_parallel_action_workflow(self) -> None:
        """Open the application workflow's outer atomic write boundary.

        Proposal, adjudication, check-request, kernel, and batch writes use their
        own focused savepoints.  This outer savepoint makes those writes one
        recoverable unit without committing a request-owned transaction.
        """

        self.begin_immediate()
        self.connection.execute("SAVEPOINT parallel_action_workflow")

    def finish_parallel_action_workflow(self) -> None:
        self.connection.execute("RELEASE SAVEPOINT parallel_action_workflow")

    def rollback_parallel_action_workflow(self) -> None:
        self.connection.execute("ROLLBACK TO SAVEPOINT parallel_action_workflow")
        self.connection.execute("RELEASE SAVEPOINT parallel_action_workflow")

    def create_parallel_action_batch(
        self,
        *,
        campaign_id: str,
        session_id: str,
        run_id: str,
        module_run_version: int,
        contract_version_id: str,
        base_state_version: int,
        idempotency_key: str,
        items: Sequence[Mapping[str, Any]],
        created_by_member_id: str | None = None,
    ) -> dict[str, Any]:
        authority = self._batch_authority()
        normalized_items = authority.normalize_items(items)
        if not 2 <= len(normalized_items) <= 12:
            raise ValueError("Parallel batches require between 2 and 12 actions")
        normalized_key = idempotency_key.strip()
        if not 8 <= len(normalized_key) <= 200:
            raise ValueError(
                "Parallel batch idempotency key must contain 8-200 characters"
            )
        if type(base_state_version) is not int or base_state_version < 0:
            raise ValueError(
                "Parallel batch base state version must be non-negative"
            )
        action_set_hash = authority.hash_value(
            [item["action_id"] for item in normalized_items]
        )
        preparation_hash = authority.preparation_hash(
            campaign_id=campaign_id,
            session_id=session_id,
            run_id=run_id,
            module_run_version=module_run_version,
            contract_version_id=contract_version_id,
            base_state_version=base_state_version,
            items=normalized_items,
        )

        self.begin_immediate()
        self.connection.execute("SAVEPOINT create_parallel_action_batch")
        try:
            # The idempotency and immutable-action checks share the write lock
            # with insertion so two connections cannot both claim one action.
            existing = self._find_parallel_action_batch_by_key(
                campaign_id, normalized_key
            )
            if existing is not None:
                if existing["preparation_hash"] != preparation_hash:
                    raise ValueError(
                        "Idempotency key belongs to a different parallel action batch"
                    )
                self.connection.execute(
                    "RELEASE SAVEPOINT create_parallel_action_batch"
                )
                return existing

            authority.validate_scope(
                campaign_id=campaign_id,
                session_id=session_id,
                run_id=run_id,
                module_run_version=module_run_version,
                contract_version_id=contract_version_id,
                base_state_version=base_state_version,
                created_by_member_id=created_by_member_id,
            )
            authority.validate_items(
                campaign_id=campaign_id,
                session_id=session_id,
                run_id=run_id,
                base_state_version=base_state_version,
                items=normalized_items,
            )
            authority.assert_actions_not_batched(normalized_items)

            batch_id = new_id("parallelbatch")
            self.connection.execute(
                """
                INSERT INTO parallel_action_batches
                  (id, campaign_id, session_id, run_id, module_run_version,
                   contract_version_id, base_state_version, idempotency_key,
                   action_set_hash, preparation_hash, created_by_member_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    campaign_id,
                    session_id,
                    run_id,
                    module_run_version,
                    contract_version_id,
                    base_state_version,
                    normalized_key,
                    action_set_hash,
                    preparation_hash,
                    created_by_member_id,
                ),
            )
            for item in normalized_items:
                self._insert_parallel_action_batch_item(batch_id, item)
            self._append_parallel_batch_event(
                batch_id,
                version=1,
                event_type="created",
                actor_member_id=created_by_member_id,
                payload={
                    "action_set_hash": action_set_hash,
                    "preparation_hash": preparation_hash,
                    "module_run_version": module_run_version,
                },
            )
            self.connection.execute(
                "RELEASE SAVEPOINT create_parallel_action_batch"
            )
        except Exception:
            self.connection.execute(
                "ROLLBACK TO SAVEPOINT create_parallel_action_batch"
            )
            self.connection.execute(
                "RELEASE SAVEPOINT create_parallel_action_batch"
            )
            raise
        return self.get_parallel_action_batch(batch_id)

    def get_parallel_action_batch(self, batch_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM parallel_action_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Parallel action batch not found: {batch_id}")
        return self._batch_authority().decode_batch(row)

    def list_parallel_action_batch_coordinator_summaries(
        self,
        campaign_id: str,
        session_id: str,
        *,
        status: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return bounded, session-scoped KP recovery metadata.

        The recovery index deliberately does not hydrate full batches: those
        records contain model previews, commands, and per-player decisions.
        Correlated counts also avoid multiplying confirmations when one action
        has more than one historical check.
        """

        if status != "needs_attention":
            raise ValueError("Only needs_attention batches can be indexed")
        if not 1 <= limit <= 100:
            raise ValueError("Parallel batch summary limit must be between 1 and 100")
        rows = self.connection.execute(
            """
            SELECT
              b.id,
              b.status,
              b.version,
              b.attention_reason,
              b.updated_at,
              (SELECT COUNT(*)
                 FROM parallel_action_batch_items item
                WHERE item.batch_id = b.id) AS participant_count,
              (SELECT COUNT(*)
                 FROM parallel_action_batch_items item
                 JOIN player_action_adjudications adjudication
                   ON adjudication.id = item.adjudication_id
                WHERE item.batch_id = b.id
                  AND adjudication.status = 'confirmed') AS confirmed_count,
              (SELECT COUNT(*)
                 FROM parallel_action_batch_items item
                 JOIN skill_checks skill_check
                   ON skill_check.player_action_id = item.action_id
                  AND skill_check.proposal_id = item.proposal_id
                WHERE item.batch_id = b.id
                  AND skill_check.status = 'requested') AS pending_check_count
            FROM parallel_action_batches b
            WHERE b.campaign_id = ?
              AND b.session_id = ?
              AND b.status = ?
            ORDER BY b.updated_at DESC, b.id DESC
            LIMIT ?
            """,
            (campaign_id, session_id, status, limit),
        ).fetchall()
        decisions = ParallelActionAbandonmentAuthority(
            self.connection
        ).evaluate_many([str(row["id"]) for row in rows])
        return [
            {
                "id": str(row["id"]),
                "status": str(row["status"]),
                "version": int(row["version"]),
                "attention_reason": str(row["attention_reason"]),
                "updated_at": str(row["updated_at"]),
                "participant_count": int(row["participant_count"]),
                "confirmed_count": int(row["confirmed_count"]),
                "pending_check_count": int(row["pending_check_count"]),
                **decisions[str(row["id"])].as_dict(),
            }
            for row in rows
        ]

    def get_parallel_action_abandonment_decision(
        self, batch_id: str
    ) -> dict[str, Any]:
        """Return the server-authoritative outcome-fishing decision."""

        return ParallelActionAbandonmentAuthority(self.connection).evaluate(
            batch_id
        ).as_dict()

    def get_parallel_action_batch_by_key(
        self, campaign_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        result = self._find_parallel_action_batch_by_key(
            campaign_id, idempotency_key.strip()
        )
        if result is None:
            raise KeyError("Parallel action batch not found")
        return result

    def get_active_parallel_action_batch_for_action(
        self, action_id: str
    ) -> dict[str, Any] | None:
        placeholders = ", ".join("?" for _ in _ACTIVE_STATUSES)
        rows = self.connection.execute(
            f"""
            SELECT b.id
            FROM parallel_action_batches b
            JOIN parallel_action_batch_items i ON i.batch_id = b.id
            WHERE i.action_id = ? AND b.status IN ({placeholders})
            ORDER BY b.created_at DESC, b.id DESC
            """,
            (action_id, *_ACTIVE_STATUSES),
        ).fetchall()
        if len(rows) > 1:
            raise RuntimeError("Action belongs to multiple active parallel batches")
        return (
            self.get_parallel_action_batch(str(rows[0]["id"]))
            if rows
            else None
        )

    def get_active_parallel_action_batch_for_member(
        self,
        campaign_id: str,
        session_id: str,
        member_id: str,
    ) -> dict[str, Any] | None:
        """Return the one active batch containing a member-owned action."""

        placeholders = ", ".join("?" for _ in _ACTIVE_STATUSES)
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT b.id
            FROM parallel_action_batches b
            JOIN parallel_action_batch_items i ON i.batch_id = b.id
            JOIN player_actions a ON a.id = i.action_id
            WHERE b.campaign_id = ? AND b.session_id = ?
              AND a.member_id = ? AND b.status IN ({placeholders})
            ORDER BY b.created_at DESC, b.id DESC
            """,
            (campaign_id, session_id, member_id, *_ACTIVE_STATUSES),
        ).fetchall()
        if len(rows) > 1:
            raise RuntimeError("Member belongs to multiple active parallel batches")
        return (
            self.get_parallel_action_batch(str(rows[0]["id"]))
            if rows
            else None
        )

    def get_parallel_action_batch_for_action(
        self, action_id: str
    ) -> dict[str, Any] | None:
        """Return an action's batch regardless of lifecycle status.

        Check-result write fences must continue to see terminal batches.  The
        active-only lookup intentionally omits settled and superseded records,
        so it is not an authority boundary for mutable linked checks.
        """

        row = self.connection.execute(
            """
            SELECT b.id
            FROM parallel_action_batches b
            JOIN parallel_action_batch_items i ON i.batch_id = b.id
            WHERE i.action_id = ?
            """,
            (action_id,),
        ).fetchone()
        return self.get_parallel_action_batch(str(row["id"])) if row else None

    def validate_parallel_action_batch_authority(
        self, batch_id: str
    ) -> dict[str, Any]:
        """Return a batch only while its frozen run/control basis is current."""

        batch = self.get_parallel_action_batch(batch_id)
        self._batch_authority().validate_execution_authority(batch)
        return batch

    def validate_parallel_action_batch_settlement(
        self, batch_id: str
    ) -> dict[str, Any]:
        """Verify the exact persisted receipt before replaying settlement."""

        batch = self.get_parallel_action_batch(batch_id)
        if batch["status"] != "settled":
            raise ValueError("Parallel batch has no settled receipt")
        self._batch_authority().validate_settlement_receipt(
            batch,
            settlement_hash=batch.get("settlement_hash"),
            scenario_command_batch_id=batch.get("scenario_command_batch_id"),
        )
        return batch

    def dispose_superseded_parallel_actions(
        self,
        batch_id: str,
        *,
        actor_member_id: str,
        reason: str,
    ) -> dict[str, Any]:
        return ParallelActionBatchDisposer(self.connection, self).dispose(
            batch_id,
            actor_member_id=actor_member_id,
            reason=reason,
        )

    def record_parallel_action_item_decision(
        self,
        batch_id: str,
        action_id: str,
        *,
        expected_version: int,
        selected_skill_key: str | None = None,
        outcome_key: str | None = None,
        check_result_fingerprint: str | None = None,
        actor_member_id: str | None = None,
    ) -> dict[str, Any]:
        if (
            selected_skill_key is None
            and outcome_key is None
            and check_result_fingerprint is None
        ):
            raise ValueError(
                "A selected skill, exact outcome, or check fingerprint is required"
            )
        authority = self._batch_authority()
        normalized_skill = authority.optional_text(
            selected_skill_key,
            field_name="selected_skill_key",
            maximum=120,
        )
        normalized_outcome = authority.optional_text(
            outcome_key,
            field_name="outcome_key",
            maximum=120,
        )
        normalized_check_fingerprint = (
            authority.sha256(
                check_result_fingerprint,
                field_name="check_result_fingerprint",
            )
            if check_result_fingerprint is not None
            else None
        )

        self.begin_immediate()
        self.connection.execute("SAVEPOINT update_parallel_action_item")
        try:
            batch = self.get_parallel_action_batch(batch_id)
            authority.validate_execution_authority(batch)
            item = self._item_for_decision(
                batch,
                action_id=action_id,
                selected_skill_key=normalized_skill,
                check_result_fingerprint=normalized_check_fingerprint,
            )
            actor_role = authority.validate_item_actor(
                batch,
                action_id=action_id,
                actor_member_id=actor_member_id,
            )
            if actor_role == "player" and (
                normalized_outcome is not None
                or normalized_check_fingerprint is not None
            ):
                raise PermissionError(
                    "Players cannot author deterministic check outcomes"
                )
            authority.validate_item_decision(
                item,
                selected_skill_key=normalized_skill,
                outcome_key=normalized_outcome,
                check_result_fingerprint=normalized_check_fingerprint,
            )
            updated = self.connection.execute(
                """
                UPDATE parallel_action_batches
                SET version = version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND version = ? AND status = ?
                """,
                (batch_id, expected_version, batch["status"]),
            )
            if updated.rowcount != 1:
                raise ValueError(
                    "Parallel action batch changed; refresh before updating"
                )
            self.connection.execute(
                """
                UPDATE parallel_action_batch_items
                SET selected_skill_key = COALESCE(?, selected_skill_key),
                    outcome_key = COALESCE(?, outcome_key),
                    check_result_fingerprint = COALESCE(
                      ?, check_result_fingerprint
                    )
                WHERE batch_id = ? AND action_id = ?
                """,
                (
                    normalized_skill,
                    normalized_outcome,
                    normalized_check_fingerprint,
                    batch_id,
                    action_id,
                ),
            )
            self._append_parallel_batch_event(
                batch_id,
                version=expected_version + 1,
                event_type="item_updated",
                actor_member_id=actor_member_id,
                payload={
                    "action_id": action_id,
                    "selected_skill_key": normalized_skill,
                    "outcome_key": normalized_outcome,
                    "check_result_fingerprint": normalized_check_fingerprint,
                },
            )
            self.connection.execute(
                "RELEASE SAVEPOINT update_parallel_action_item"
            )
        except Exception:
            self.connection.execute(
                "ROLLBACK TO SAVEPOINT update_parallel_action_item"
            )
            self.connection.execute(
                "RELEASE SAVEPOINT update_parallel_action_item"
            )
            raise
        return self.get_parallel_action_batch(batch_id)

    def rebind_parallel_action_item_skill(
        self,
        batch_id: str,
        action_id: str,
        *,
        expected_batch_version: int,
        expected_adjudication_version: int,
        selected_skill_key: str,
        preview: Mapping[str, Any],
        narrative: Mapping[str, Any],
        actor_member_id: str,
    ) -> dict[str, Any]:
        return ParallelActionSkillRebinder(self.connection, self).rebind(
            batch_id,
            action_id,
            expected_batch_version=expected_batch_version,
            expected_adjudication_version=expected_adjudication_version,
            selected_skill_key=selected_skill_key,
            preview=preview,
            narrative=narrative,
            actor_member_id=actor_member_id,
        )

    def transition_parallel_action_batch(
        self,
        batch_id: str,
        event: ParallelBatchEvent,
        *,
        expected_version: int,
        actor_member_id: str | None = None,
        reason: str = "",
        settlement_hash: str | None = None,
        scenario_command_batch_id: str | None = None,
    ) -> dict[str, Any]:
        authority = self._batch_authority()
        self.begin_immediate()
        self.connection.execute("SAVEPOINT transition_parallel_action_batch")
        try:
            batch = self.get_parallel_action_batch(batch_id)
            if event in {"request_attention", "supersede"}:
                authority.validate_attention_actor(batch, actor_member_id)
            else:
                authority.validate_batch_actor(
                    batch, actor_member_id, require_kp=True
                )
            if event not in {
                "request_attention",
                "supersede",
                # The kernel may legitimately advance/complete the frozen run.
                # Its exact command-batch receipt is the authority at this point.
                "commit_succeeded",
            }:
                authority.validate_execution_authority(batch)
            if batch["version"] != expected_version:
                raise ValueError(
                    "Parallel action batch changed; refresh before transitioning"
                )
            transition = self._parallel_batch_state_machine.transition(
                ParallelBatchState(
                    status=batch["status"], version=batch["version"]
                ),
                event,
            )
            authority.validate_transition(batch, event)
            normalized_reason = reason.strip()
            if len(normalized_reason) > 2000:
                raise ValueError("Parallel batch attention reason is too long")
            if event == "request_attention" and not normalized_reason:
                raise ValueError("Requesting attention requires a reason")
            if event == "commit_succeeded":
                authority.validate_settlement_receipt(
                    batch,
                    settlement_hash=settlement_hash,
                    scenario_command_batch_id=scenario_command_batch_id,
                )
            elif settlement_hash is not None or scenario_command_batch_id is not None:
                raise ValueError(
                    "Only a successful commit can attach a settlement receipt"
                )

            attention_reason = batch["attention_reason"]
            if event == "request_attention":
                attention_reason = normalized_reason
            elif event.startswith("resume_"):
                attention_reason = ""
            updated = self.connection.execute(
                """
                UPDATE parallel_action_batches
                SET status = ?, version = ?, attention_reason = ?,
                    settlement_hash = ?, scenario_command_batch_id = ?,
                    settled_at = CASE WHEN ? = 'settled' THEN CURRENT_TIMESTAMP END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND version = ? AND status = ?
                """,
                (
                    transition.current.status,
                    transition.current.version,
                    attention_reason,
                    settlement_hash,
                    scenario_command_batch_id,
                    transition.current.status,
                    batch_id,
                    expected_version,
                    transition.previous.status,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError(
                    "Parallel action batch changed; refresh before transitioning"
                )
            self._append_parallel_batch_event(
                batch_id,
                version=transition.current.version,
                event_type=event,
                actor_member_id=actor_member_id,
                payload={
                    "reason": normalized_reason,
                    "settlement_hash": settlement_hash,
                    "scenario_command_batch_id": scenario_command_batch_id,
                },
            )
            self.connection.execute(
                "RELEASE SAVEPOINT transition_parallel_action_batch"
            )
        except Exception:
            self.connection.execute(
                "ROLLBACK TO SAVEPOINT transition_parallel_action_batch"
            )
            self.connection.execute(
                "RELEASE SAVEPOINT transition_parallel_action_batch"
            )
            raise
        return self.get_parallel_action_batch(batch_id)

    def _find_parallel_action_batch_by_key(
        self, campaign_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM parallel_action_batches
            WHERE campaign_id = ? AND idempotency_key = ?
            """,
            (campaign_id, idempotency_key),
        ).fetchone()
        return self._batch_authority().decode_batch(row) if row is not None else None

    def _insert_parallel_action_batch_item(
        self,
        batch_id: str,
        item: dict[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO parallel_action_batch_items
              (batch_id, action_id, proposal_id, adjudication_id, actor_id,
               operator_id, preview_hash, selected_skill_key, outcome_key,
               check_result_fingerprint, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                item["action_id"],
                item["proposal_id"],
                item["adjudication_id"],
                item["actor_id"],
                item["operator_id"],
                item["preview_hash"],
                item["selected_skill_key"],
                item["outcome_key"],
                item["check_result_fingerprint"],
                item["priority"],
            ),
        )

    @staticmethod
    def _item_for_decision(
        batch: dict[str, Any],
        *,
        action_id: str,
        selected_skill_key: str | None,
        check_result_fingerprint: str | None,
    ) -> dict[str, Any]:
        if batch["status"] not in {"awaiting_confirmation", "awaiting_checks"}:
            raise ValueError(
                "Parallel item decisions require a batch awaiting consent or checks"
            )
        if (
            selected_skill_key is not None
            and batch["status"] != "awaiting_confirmation"
        ):
            raise ValueError(
                "Skill selection is closed after confirmation completes"
            )
        if (
            check_result_fingerprint is not None
            and batch["status"] != "awaiting_checks"
        ):
            raise ValueError("Check results can be bound only while awaiting checks")
        item = next(
            (
                candidate
                for candidate in batch["items"]
                if candidate["action_id"] == action_id
            ),
            None,
        )
        if item is None:
            raise KeyError(f"Parallel batch action not found: {action_id}")
        return item

    def _append_parallel_batch_event(
        self,
        batch_id: str,
        *,
        version: int,
        event_type: str,
        actor_member_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO parallel_action_batch_events
              (id, batch_id, version, event_type, actor_member_id, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("parallelbatchevent"),
                batch_id,
                version,
                event_type,
                actor_member_id,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )

    def _batch_authority(self) -> ParallelActionBatchAuthority:
        return ParallelActionBatchAuthority(self.connection)

    # Retain this private compatibility seam for focused repository tests while
    # keeping the implementation in the authority collaborator.
    def _authoritative_check_result(
        self, item: dict[str, Any]
    ) -> tuple[str, str]:
        return self._batch_authority().authoritative_check_result(item)


__all__ = ["ParallelActionBatchRepository"]
