"""Persistence for restart-safe encounter action previews and consent."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class EncounterActionRepository(SQLiteRepository):
    def create_encounter_action_request(
        self,
        *,
        encounter_id: str,
        campaign_id: str,
        session_id: str,
        member_id: str,
        participant_id: str,
        client_action_id: str,
        encounter_version: int,
        action_text: str,
        action_key: str,
        target_id: str | None,
        status: str,
        preview: dict[str, Any],
    ) -> dict[str, Any]:
        request_id = new_id("encaction")
        self.connection.execute(
            """
            INSERT INTO encounter_action_requests
              (id, encounter_id, campaign_id, session_id, member_id,
               participant_id, client_action_id, encounter_version,
               action_text, action_key, target_id, status, preview_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, client_action_id) DO NOTHING
            """,
            (
                request_id,
                encounter_id,
                campaign_id,
                session_id,
                member_id,
                participant_id,
                client_action_id,
                encounter_version,
                action_text,
                action_key,
                target_id,
                status,
                json.dumps(preview, ensure_ascii=False, sort_keys=True),
            ),
        )
        row = self.connection.execute(
            """
            SELECT id FROM encounter_action_requests
            WHERE session_id = ? AND client_action_id = ?
            """,
            (session_id, client_action_id),
        ).fetchone()
        result = self.get_encounter_action_request(str(row["id"]))
        expected = {
            "encounter_id": encounter_id,
            "campaign_id": campaign_id,
            "session_id": session_id,
            "member_id": member_id,
            "participant_id": participant_id,
            "encounter_version": encounter_version,
            "action_text": action_text,
            "action_key": action_key,
            "target_id": target_id,
        }
        if any(result[key] != value for key, value in expected.items()):
            raise ValueError("Encounter action idempotency key was reused")
        return result

    def get_encounter_action_request(self, request_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM encounter_action_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Encounter action request not found: {request_id}")
        return self._decode(row)

    def get_active_encounter_action_request(
        self, encounter_id: str, member_id: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM encounter_action_requests
            WHERE encounter_id = ? AND member_id = ?
              AND status IN ('awaiting_confirmation', 'needs_attention', 'confirmed')
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (encounter_id, member_id),
        ).fetchone()
        return self._decode(row) if row is not None else None

    def checkpoint_encounter_action_command(
        self,
        request_id: str,
        *,
        expected_version: int,
        prepared_command: dict[str, Any],
    ) -> dict[str, Any]:
        updated = self.connection.execute(
            """
            UPDATE encounter_action_requests
            SET status = 'confirmed', prepared_command_json = ?,
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ? AND status = 'awaiting_confirmation'
            """,
            (
                json.dumps(prepared_command, ensure_ascii=False, sort_keys=True),
                request_id,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Encounter action changed; refresh before confirming")
        return self.get_encounter_action_request(request_id)

    def apply_encounter_action_agent_proposal(
        self,
        request_id: str,
        *,
        expected_version: int,
        action_key: str,
        target_id: str | None,
        status: str,
        preview: dict[str, Any],
    ) -> dict[str, Any]:
        if status not in {"awaiting_confirmation", "needs_attention"}:
            raise ValueError("Encounter agent proposal status is invalid")
        updated = self.connection.execute(
            """
            UPDATE encounter_action_requests
            SET action_key = ?, target_id = ?, status = ?, preview_json = ?,
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ? AND status = 'needs_attention'
            """,
            (
                action_key,
                target_id,
                status,
                json.dumps(preview, ensure_ascii=False, sort_keys=True),
                request_id,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Encounter intent changed; refresh before applying the proposal")
        return self.get_encounter_action_request(request_id)

    def complete_encounter_action_request(
        self,
        request_id: str,
        *,
        expected_version: int,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        updated = self.connection.execute(
            """
            UPDATE encounter_action_requests
            SET status = 'committed', result_json = ?, version = version + 1,
                updated_at = CURRENT_TIMESTAMP, committed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ? AND status = 'confirmed'
            """,
            (
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                request_id,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Encounter action completion changed concurrently")
        return self.get_encounter_action_request(request_id)

    def cancel_encounter_action_request(
        self, request_id: str, *, expected_version: int
    ) -> dict[str, Any]:
        updated = self.connection.execute(
            """
            UPDATE encounter_action_requests
            SET status = 'cancelled', version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ?
              AND status IN ('awaiting_confirmation', 'needs_attention')
            """,
            (request_id, expected_version),
        )
        if updated.rowcount != 1:
            raise ValueError("Only an unconfirmed encounter action may be changed")
        return self.get_encounter_action_request(request_id)

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        result["preview"] = decode_json_field(result.pop("preview_json"), {})
        raw_command = result.pop("prepared_command_json")
        result["prepared_command"] = (
            decode_json_field(raw_command, {}) if raw_command is not None else None
        )
        raw_result = result.pop("result_json")
        result["result"] = (
            decode_json_field(raw_result, {}) if raw_result is not None else None
        )
        return result


__all__ = ["EncounterActionRepository"]
