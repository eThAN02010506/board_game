"""Durable checkpoints for automated encounter turns."""

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class EncounterAutomationRepository(SQLiteRepository):
    def create_encounter_automation_turn(self, **values: Any) -> dict[str, Any]:
        turn_id = str(values.get("id") or new_id("encauto"))
        self.connection.execute(
            """
            INSERT INTO encounter_automation_turns
              (id, encounter_id, campaign_id, session_id, encounter_version,
               participant_id, phase, policy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(encounter_id, encounter_version, participant_id, phase)
            DO NOTHING
            """,
            (
                turn_id,
                values["encounter_id"],
                values["campaign_id"],
                values["session_id"],
                values["encounter_version"],
                values["participant_id"],
                values["phase"],
                values["policy"],
            ),
        )
        row = self.connection.execute(
            """
            SELECT id FROM encounter_automation_turns
            WHERE encounter_id = ? AND encounter_version = ?
              AND participant_id = ? AND phase = ?
            """,
            (
                values["encounter_id"],
                values["encounter_version"],
                values["participant_id"],
                values["phase"],
            ),
        ).fetchone()
        assert row is not None
        result = self.get_encounter_automation_turn(str(row["id"]))
        if any(
            result[key] != values[key]
            for key in (
                "campaign_id",
                "session_id",
                "policy",
            )
        ):
            raise ValueError("Encounter automation authority changed")
        return result

    def get_encounter_automation_turn(self, turn_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM encounter_automation_turns WHERE id = ?", (turn_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Encounter automation turn not found: {turn_id}")
        return self._decode_automation_turn(row)

    def get_encounter_automation_turn_for_version(
        self,
        encounter_id: str,
        encounter_version: int,
        participant_id: str,
        phase: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM encounter_automation_turns
            WHERE encounter_id = ? AND encounter_version = ?
              AND participant_id = ? AND phase = ?
            """,
            (encounter_id, encounter_version, participant_id, phase),
        ).fetchone()
        return self._decode_automation_turn(row) if row is not None else None

    def checkpoint_encounter_automation_turn(
        self,
        turn_id: str,
        *,
        expected_version: int,
        selection: dict[str, Any],
        prepared_command: dict[str, Any],
    ) -> dict[str, Any]:
        updated = self.connection.execute(
            """
            UPDATE encounter_automation_turns
            SET status = 'checkpointed', selection_json = ?,
                prepared_command_json = ?, version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ? AND status = 'planned'
            """,
            (
                json.dumps(selection, ensure_ascii=False, sort_keys=True),
                json.dumps(prepared_command, ensure_ascii=False, sort_keys=True),
                turn_id,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Encounter automation checkpoint changed")
        return self.get_encounter_automation_turn(turn_id)

    def complete_encounter_automation_turn(
        self,
        turn_id: str,
        *,
        expected_version: int,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        updated = self.connection.execute(
            """
            UPDATE encounter_automation_turns
            SET status = 'committed', result_json = ?, version = version + 1,
                updated_at = CURRENT_TIMESTAMP, committed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ? AND status = 'checkpointed'
            """,
            (
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                turn_id,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            current = self.get_encounter_automation_turn(turn_id)
            if current["status"] == "committed":
                return current
            raise ValueError("Encounter automation completion changed")
        return self.get_encounter_automation_turn(turn_id)

    @staticmethod
    def _decode_automation_turn(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        result["selection"] = decode_json_field(result.pop("selection_json"), {})
        command = result.pop("prepared_command_json")
        result["prepared_command"] = (
            decode_json_field(command, {}) if command is not None else None
        )
        raw_result = result.pop("result_json")
        result["result"] = (
            decode_json_field(raw_result, {}) if raw_result is not None else None
        )
        return result


__all__ = ["EncounterAutomationRepository"]
