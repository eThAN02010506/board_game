"""Append-only persistence and stable projection for human-KP advice audits."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository

_TERMINAL_EVENT_TYPES = frozenset(
    {"completed", "failed", "cancelled_client", "cancelled_control", "rejected_busy"}
)


class DirectorHelpAuditRepository(SQLiteRepository):
    def append_abandoned_director_help_terminals(self, *, error_code: str) -> int:
        """Atomically add a failed terminal for every still-open request."""

        requested_rows = self.connection.execute(
            """
            SELECT requested.attempt_id, requested.campaign_id, requested.run_id,
                   requested.request_id
            FROM director_help_audit_events requested
            WHERE requested.event_type = 'requested'
              AND NOT EXISTS (
                SELECT 1
                FROM director_help_audit_events terminal
                WHERE terminal.attempt_id = requested.attempt_id
                  AND terminal.event_type <> 'requested'
              )
            ORDER BY requested.sequence
            """
        ).fetchall()
        recovered = 0
        for requested in requested_rows:
            cursor = self.connection.execute(
                """
                INSERT INTO director_help_audit_events
                  (event_id, attempt_id, campaign_id, run_id, requested_by_member_id,
                   request_id, event_type, error_code, duration_ms)
                SELECT ?, ?, ?, ?, NULL, ?, 'failed', ?, 0
                WHERE NOT EXISTS (
                  SELECT 1
                  FROM director_help_audit_events terminal
                  WHERE terminal.attempt_id = ?
                    AND terminal.event_type <> 'requested'
                )
                """,
                (
                    new_id("director_help_audit"),
                    requested["attempt_id"],
                    requested["campaign_id"],
                    requested["run_id"],
                    requested["request_id"],
                    error_code,
                    requested["attempt_id"],
                ),
            )
            recovered += max(cursor.rowcount, 0)
        return recovered

    def append_director_help_audit_event(
        self,
        *,
        attempt_id: str,
        campaign_id: str,
        run_id: str,
        requested_by_member_id: str | None,
        request_id: str,
        event_type: str,
        question: str | None = None,
        advice: dict[str, Any] | None = None,
        response_hash: str | None = None,
        error_code: str | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        if event_type in _TERMINAL_EVENT_TYPES:
            self._require_matching_requested_event(
                attempt_id=attempt_id,
                campaign_id=campaign_id,
                run_id=run_id,
                request_id=request_id,
            )

        event_id = new_id("director_help_audit")
        advice_json = (
            None
            if advice is None
            else json.dumps(advice, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        self.connection.execute(
            """
            INSERT INTO director_help_audit_events
              (event_id, attempt_id, campaign_id, run_id, requested_by_member_id,
               request_id, event_type, question, advice_json, response_hash,
               error_code, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                attempt_id,
                campaign_id,
                run_id,
                requested_by_member_id,
                request_id,
                event_type,
                question,
                advice_json,
                response_hash,
                error_code,
                duration_ms,
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM director_help_audit_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Director Help audit event was not persisted")
        return DirectorHelpAuditRepository._decode_director_help_event(row)

    def list_director_help_audits(
        self,
        campaign_id: str,
        limit: int,
        before_id: str | None = None,
    ) -> list[dict[str, Any]]:
        before_sequence: int | None = None
        if before_id is not None:
            cursor = self.connection.execute(
                """
                SELECT sequence
                FROM director_help_audit_events
                WHERE campaign_id = ? AND attempt_id = ? AND event_type = 'requested'
                """,
                (campaign_id, before_id),
            ).fetchone()
            if cursor is None:
                raise KeyError(f"Director Help audit cursor not found: {before_id}")
            before_sequence = int(cursor["sequence"])

        rows = self.connection.execute(
            """
            SELECT requested.attempt_id AS id,
                   requested.run_id AS run_id,
                   requested.requested_by_member_id AS requested_by_member_id,
                   requested.question AS question,
                   terminal.event_type AS terminal_event_type,
                   terminal.error_code AS error_code,
                   terminal.duration_ms AS duration_ms,
                   requested.created_at AS created_at,
                   terminal.created_at AS completed_at,
                   terminal.response_hash AS response_hash,
                   terminal.advice_json AS advice_json
            FROM director_help_audit_events requested
            LEFT JOIN director_help_audit_events terminal
              ON terminal.attempt_id = requested.attempt_id
             AND terminal.campaign_id = requested.campaign_id
             AND terminal.event_type <> 'requested'
            WHERE requested.campaign_id = ?
              AND requested.event_type = 'requested'
              AND (? IS NULL OR requested.sequence < ?)
            ORDER BY requested.sequence DESC
            LIMIT ?
            """,
            (
                campaign_id,
                before_sequence,
                before_sequence,
                max(1, min(int(limit), 200)),
            ),
        ).fetchall()
        return [
            DirectorHelpAuditRepository._project_director_help_audit(row)
            for row in rows
        ]

    def _require_matching_requested_event(
        self,
        *,
        attempt_id: str,
        campaign_id: str,
        run_id: str,
        request_id: str,
    ) -> None:
        requested = self.connection.execute(
            """
            SELECT campaign_id, run_id, request_id
            FROM director_help_audit_events
            WHERE attempt_id = ? AND event_type = 'requested'
            """,
            (attempt_id,),
        ).fetchone()
        if requested is None:
            raise ValueError("Director Help terminal event requires a requested event")
        expected = (
            str(requested["campaign_id"]),
            str(requested["run_id"]),
            str(requested["request_id"]),
        )
        actual = (campaign_id, run_id, request_id)
        if actual != expected:
            raise ValueError("Director Help terminal event does not match its request")

    @staticmethod
    def _decode_director_help_event(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        advice_json = result.pop("advice_json")
        result["advice"] = (
            None if advice_json is None else decode_json_field(advice_json, None)
        )
        return result

    @staticmethod
    def _project_director_help_audit(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        result["outcome"] = result.pop("terminal_event_type") or "requested"
        advice_json = result.pop("advice_json")
        result["advice"] = (
            None if advice_json is None else decode_json_field(advice_json, None)
        )
        return result


__all__ = ["DirectorHelpAuditRepository"]
