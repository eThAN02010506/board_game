"""Durable coordination for consent-bound parallel action regrouping."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.sqlite import SQLiteRepository

_ACTIVE_REGATHER_STATUSES = ("gathering", "queued")


class ParallelActionRegatherRepository(SQLiteRepository):
    """Persist the participant barrier between revision and normal planning.

    This repository deliberately stores no model interpretation or rule result.
    Once every original participant has explicitly submitted, the existing
    parallel planning workflow receives the replacement action IDs unchanged.
    """

    def create_parallel_action_regather(
        self,
        source_batch_id: str,
        *,
        actor_member_id: str | None,
        reason: str,
    ) -> dict[str, Any]:
        normalized_reason = reason.strip()
        if not normalized_reason or len(normalized_reason) > 2000:
            raise ValueError("Regather reason must contain 1-2000 characters")
        self.begin_immediate()
        self.connection.execute("SAVEPOINT create_parallel_action_regather")
        try:
            existing = self._regather_for_source(source_batch_id)
            if existing is not None:
                self.connection.execute("RELEASE SAVEPOINT create_parallel_action_regather")
                return existing
            batch = self.get_parallel_action_batch(source_batch_id)
            if batch["status"] != "superseded":
                raise ValueError("Only a superseded parallel batch can be regrouped")
            members = self._source_members(source_batch_id)
            if not 2 <= len(members) <= 12:
                raise ValueError("Regather requires 2-12 original participants")
            if actor_member_id is not None and actor_member_id not in {
                item["member_id"] for item in members
            }:
                raise PermissionError("Only an original participant can start regrouping")
            for item in members:
                active = self._active_regather_row_for_member(
                    str(batch["campaign_id"]),
                    str(batch["session_id"]),
                    str(item["member_id"]),
                )
                if active is not None:
                    raise ValueError("A participant already belongs to an active regather")
            regather_id = new_id("parallelregather")
            self.connection.execute(
                """
                INSERT INTO parallel_action_regathers
                  (id, source_batch_id, campaign_id, session_id, source_run_id,
                   source_module_run_version, source_state_version,
                   created_by_member_id, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    regather_id,
                    source_batch_id,
                    batch["campaign_id"],
                    batch["session_id"],
                    batch["run_id"],
                    batch["module_run_version"],
                    batch["base_state_version"],
                    actor_member_id,
                    normalized_reason,
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO parallel_action_regather_members
                  (regather_id, member_id, prior_action_id)
                VALUES (?, ?, ?)
                """,
                [
                    (regather_id, item["member_id"], item["prior_action_id"])
                    for item in members
                ],
            )
            self._append_regather_event(
                regather_id,
                version=1,
                event_type="created",
                actor_member_id=actor_member_id,
                payload={"participant_count": len(members)},
            )
            self.connection.execute("RELEASE SAVEPOINT create_parallel_action_regather")
        except Exception:
            self.connection.execute("ROLLBACK TO SAVEPOINT create_parallel_action_regather")
            self.connection.execute("RELEASE SAVEPOINT create_parallel_action_regather")
            raise
        return self.get_parallel_action_regather(regather_id)

    def get_parallel_action_regather(self, regather_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM parallel_action_regathers WHERE id = ?", (regather_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Parallel action regather not found: {regather_id}")
        return self._decode_regather(row)

    def get_active_parallel_action_regather_for_member(
        self,
        campaign_id: str,
        session_id: str,
        member_id: str,
    ) -> dict[str, Any] | None:
        row = self._active_regather_row_for_member(
            campaign_id, session_id, member_id
        )
        return self._decode_regather(row) if row is not None else None

    def register_parallel_action_regather_submission(
        self,
        regather_id: str,
        action_id: str,
        *,
        actor_member_id: str,
        expected_version: int,
        auto_kp_requested: bool,
    ) -> dict[str, Any]:
        if not auto_kp_requested:
            raise ValueError("Regather submissions require explicit Auto KP consent")
        self.begin_immediate()
        self.connection.execute("SAVEPOINT register_parallel_regather_submission")
        try:
            regather = self.get_parallel_action_regather(regather_id)
            if regather["status"] != "gathering":
                existing = self._member_entry(regather_id, actor_member_id)
                if existing.get("replacement_action_id") == action_id:
                    self.connection.execute(
                        "RELEASE SAVEPOINT register_parallel_regather_submission"
                    )
                    return regather
                raise ValueError("Parallel regrouping is no longer accepting actions")
            member = self._member_entry(regather_id, actor_member_id)
            existing_action = member.get("replacement_action_id")
            if existing_action is not None:
                if str(existing_action) != action_id:
                    raise ValueError("Participant already submitted a replacement action")
                self.connection.execute(
                    "RELEASE SAVEPOINT register_parallel_regather_submission"
                )
                return regather
            action = self.get_player_action(action_id)
            if (
                str(action["campaign_id"]) != str(regather["campaign_id"])
                or str(action["session_id"]) != str(regather["session_id"])
                or str(action["member_id"]) != actor_member_id
                or str(action["status"]) != "submitted"
            ):
                raise PermissionError("Replacement action does not match regather authority")
            updated = self.connection.execute(
                """
                UPDATE parallel_action_regathers
                SET version = version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'gathering' AND version = ?
                """,
                (regather_id, expected_version),
            )
            if updated.rowcount != 1:
                raise ValueError("Parallel regather changed; refresh before submitting")
            self.connection.execute(
                """
                UPDATE parallel_action_regather_members
                SET replacement_action_id = ?, auto_kp_requested = 1,
                    submitted_at = CURRENT_TIMESTAMP
                WHERE regather_id = ? AND member_id = ?
                  AND replacement_action_id IS NULL
                """,
                (action_id, regather_id, actor_member_id),
            )
            self._append_regather_event(
                regather_id,
                version=expected_version + 1,
                event_type="member_submitted",
                actor_member_id=actor_member_id,
                payload={"action_id": action_id},
            )
            self.connection.execute(
                "RELEASE SAVEPOINT register_parallel_regather_submission"
            )
        except Exception:
            self.connection.execute(
                "ROLLBACK TO SAVEPOINT register_parallel_regather_submission"
            )
            self.connection.execute(
                "RELEASE SAVEPOINT register_parallel_regather_submission"
            )
            raise
        return self.get_parallel_action_regather(regather_id)

    def mark_parallel_action_regather_queued(
        self,
        regather_id: str,
        *,
        expected_version: int,
        prepare_job_id: str,
    ) -> dict[str, Any]:
        regather = self.get_parallel_action_regather(regather_id)
        if regather["status"] == "queued":
            if str(regather.get("prepare_job_id")) != prepare_job_id:
                raise ValueError("Regather is bound to another prepare job")
            return regather
        if regather["status"] != "gathering" or not regather["ready"]:
            raise ValueError("Regather is not ready for parallel planning")
        updated = self.connection.execute(
            """
            UPDATE parallel_action_regathers
            SET status = 'queued', version = version + 1,
                prepare_job_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'gathering' AND version = ?
            """,
            (prepare_job_id, regather_id, expected_version),
        )
        if updated.rowcount != 1:
            raise ValueError("Parallel regather changed before queue binding")
        self._append_regather_event(
            regather_id,
            version=expected_version + 1,
            event_type="queued",
            actor_member_id=None,
            payload={"prepare_job_id": prepare_job_id},
        )
        return self.get_parallel_action_regather(regather_id)

    def complete_parallel_action_regather(
        self,
        regather_id: str,
        *,
        resulting_batch_id: str | None,
    ) -> dict[str, Any]:
        regather = self.get_parallel_action_regather(regather_id)
        completion_kind = "batch" if resulting_batch_id else "fallback"
        if regather["status"] == "completed":
            if (
                regather.get("completion_kind") != completion_kind
                or regather.get("resulting_batch_id") != resulting_batch_id
            ):
                raise ValueError("Regather completion receipt does not match")
            return regather
        if regather["status"] != "queued":
            raise ValueError("Only a queued regather can complete")
        if resulting_batch_id is not None:
            batch = self.get_parallel_action_batch(resulting_batch_id)
            if (
                str(batch["campaign_id"]) != str(regather["campaign_id"])
                or str(batch["session_id"]) != str(regather["session_id"])
            ):
                raise PermissionError("Resulting batch crosses regather authority")
        expected_version = int(regather["version"])
        updated = self.connection.execute(
            """
            UPDATE parallel_action_regathers
            SET status = 'completed', version = version + 1,
                resulting_batch_id = ?, completion_kind = ?,
                completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'queued' AND version = ?
            """,
            (resulting_batch_id, completion_kind, regather_id, expected_version),
        )
        if updated.rowcount != 1:
            raise ValueError("Parallel regather changed before completion")
        self._append_regather_event(
            regather_id,
            version=expected_version + 1,
            event_type="completed",
            actor_member_id=None,
            payload={
                "completion_kind": completion_kind,
                "resulting_batch_id": resulting_batch_id,
            },
        )
        return self.get_parallel_action_regather(regather_id)

    def reopen_parallel_action_regather(
        self,
        regather_id: str,
        *,
        expected_prepare_job_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Discard stale submissions and require fresh consent under new state."""

        normalized_reason = reason.strip()
        if not normalized_reason or len(normalized_reason) > 2000:
            raise ValueError("Reopen reason must contain 1-2000 characters")
        self.begin_immediate()
        self.connection.execute("SAVEPOINT reopen_parallel_action_regather")
        try:
            regather = self.get_parallel_action_regather(regather_id)
            if regather["status"] == "gathering" and not regather.get("prepare_job_id"):
                self.connection.execute("RELEASE SAVEPOINT reopen_parallel_action_regather")
                return regather
            if (
                regather["status"] != "queued"
                or str(regather.get("prepare_job_id") or "")
                != expected_prepare_job_id
            ):
                raise ValueError("Only the exact queued prepare may reopen a regather")
            replacement_ids = tuple(
                str(item["replacement_action_id"])
                for item in regather["members"]
                if item.get("replacement_action_id")
            )
            for action_id in replacement_ids:
                action = self.get_player_action(action_id)
                if action["status"] != "submitted" or action.get("proposal_id"):
                    raise ValueError("Cannot reopen a regather after planning began")
            if replacement_ids:
                placeholders = ", ".join("?" for _ in replacement_ids)
                updated_actions = self.connection.execute(
                    f"""
                    UPDATE player_actions
                    SET status = 'rejected', resolved_at = CURRENT_TIMESTAMP
                    WHERE id IN ({placeholders}) AND status = 'submitted'
                      AND proposal_id IS NULL
                    """,
                    replacement_ids,
                )
                if updated_actions.rowcount != len(replacement_ids):
                    raise ValueError("Regather actions changed during stale recovery")
            expected_version = int(regather["version"])
            self.connection.execute(
                """
                UPDATE parallel_action_regather_members
                SET replacement_action_id = NULL, auto_kp_requested = 0,
                    submitted_at = NULL
                WHERE regather_id = ?
                """,
                (regather_id,),
            )
            updated = self.connection.execute(
                """
                UPDATE parallel_action_regathers
                SET status = 'gathering', version = version + 1,
                    prepare_job_id = NULL, reason = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'queued' AND version = ?
                """,
                (normalized_reason, regather_id, expected_version),
            )
            if updated.rowcount != 1:
                raise ValueError("Regather changed during stale recovery")
            self._append_regather_event(
                regather_id,
                version=expected_version + 1,
                event_type="reopened",
                actor_member_id=None,
                payload={"reason": normalized_reason},
            )
            self.connection.execute("RELEASE SAVEPOINT reopen_parallel_action_regather")
        except Exception:
            self.connection.execute("ROLLBACK TO SAVEPOINT reopen_parallel_action_regather")
            self.connection.execute("RELEASE SAVEPOINT reopen_parallel_action_regather")
            raise
        return self.get_parallel_action_regather(regather_id)

    def cancel_parallel_action_regather(
        self,
        regather_id: str,
        *,
        expected_version: int,
        actor_member_id: str,
        reason: str,
    ) -> dict[str, Any]:
        normalized_reason = reason.strip()
        if not normalized_reason or len(normalized_reason) > 2000:
            raise ValueError("Cancellation reason must contain 1-2000 characters")
        regather = self.get_parallel_action_regather(regather_id)
        self._member_entry(regather_id, actor_member_id)
        if regather["status"] == "cancelled":
            return regather
        if regather["status"] != "gathering":
            raise ValueError("Only a gathering regather can be cancelled by a player")
        updated = self.connection.execute(
            """
            UPDATE parallel_action_regathers
            SET status = 'cancelled', version = version + 1,
                reason = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'gathering' AND version = ?
            """,
            (normalized_reason, regather_id, expected_version),
        )
        if updated.rowcount != 1:
            raise ValueError("Parallel regather changed before cancellation")
        self._append_regather_event(
            regather_id,
            version=expected_version + 1,
            event_type="cancelled",
            actor_member_id=actor_member_id,
            payload={"reason": normalized_reason},
        )
        return self.get_parallel_action_regather(regather_id)

    def _regather_for_source(self, source_batch_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM parallel_action_regathers WHERE source_batch_id = ?",
            (source_batch_id,),
        ).fetchone()
        return self._decode_regather(row) if row is not None else None

    def _active_regather_row_for_member(
        self, campaign_id: str, session_id: str, member_id: str
    ):
        placeholders = ", ".join("?" for _ in _ACTIVE_REGATHER_STATUSES)
        rows = self.connection.execute(
            f"""
            SELECT r.* FROM parallel_action_regathers r
            JOIN parallel_action_regather_members m ON m.regather_id = r.id
            WHERE r.campaign_id = ? AND r.session_id = ? AND m.member_id = ?
              AND r.status IN ({placeholders})
            ORDER BY r.created_at DESC, r.id DESC
            """,
            (campaign_id, session_id, member_id, *_ACTIVE_REGATHER_STATUSES),
        ).fetchall()
        if len(rows) > 1:
            raise RuntimeError("Member belongs to multiple active regathers")
        return rows[0] if rows else None

    def _source_members(self, batch_id: str) -> list[dict[str, str]]:
        rows = self.connection.execute(
            """
            SELECT a.member_id, i.action_id AS prior_action_id
            FROM parallel_action_batch_items i
            JOIN player_actions a ON a.id = i.action_id
            WHERE i.batch_id = ?
            ORDER BY a.created_at, a.id
            """,
            (batch_id,),
        ).fetchall()
        members = [dict(row) for row in rows]
        if len({item["member_id"] for item in members}) != len(members):
            raise RuntimeError("Source batch contains duplicate participants")
        return members

    def _member_entry(self, regather_id: str, member_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT * FROM parallel_action_regather_members
            WHERE regather_id = ? AND member_id = ?
            """,
            (regather_id, member_id),
        ).fetchone()
        if row is None:
            raise PermissionError("Member does not participate in this regather")
        return dict(row)

    def _decode_regather(self, row) -> dict[str, Any]:
        value = dict(row)
        members = [
            dict(item)
            for item in self.connection.execute(
                """
                SELECT * FROM parallel_action_regather_members
                WHERE regather_id = ? ORDER BY member_id
                """,
                (value["id"],),
            ).fetchall()
        ]
        for item in members:
            item["auto_kp_requested"] = bool(item["auto_kp_requested"])
        value["members"] = members
        value["participant_count"] = len(members)
        value["submitted_count"] = sum(
            item["replacement_action_id"] is not None for item in members
        )
        value["ready"] = bool(members) and all(
            item["replacement_action_id"] is not None
            and item["auto_kp_requested"]
            for item in members
        )
        return value

    def _append_regather_event(
        self,
        regather_id: str,
        *,
        version: int,
        event_type: str,
        actor_member_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO parallel_action_regather_events
              (id, regather_id, version, event_type, actor_member_id, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("parallelregatherevent"),
                regather_id,
                version,
                event_type,
                actor_member_id,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ),
        )


__all__ = ["ParallelActionRegatherRepository"]
