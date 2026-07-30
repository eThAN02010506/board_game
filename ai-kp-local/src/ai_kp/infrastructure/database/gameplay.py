"""SQLite persistence for replayable CoC7 gameplay aggregates."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class GameplayRepository(SQLiteRepository):
    def create_coc7_encounter(
        self,
        *,
        campaign_id: str,
        session_id: str,
        kind: str,
        title: str,
        state: dict[str, Any],
        member_id: str,
    ) -> dict[str, Any]:
        encounter_id = new_id("encounter")
        self.connection.execute(
            """
            INSERT INTO coc7_encounters
              (id, campaign_id, session_id, kind, title, state_json,
               created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                encounter_id,
                campaign_id,
                session_id,
                kind,
                title.strip(),
                json.dumps(state, ensure_ascii=False, sort_keys=True),
                member_id,
            ),
        )
        return self.get_coc7_encounter(encounter_id)

    def get_coc7_encounter(self, encounter_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM coc7_encounters WHERE id = ?",
            (encounter_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"CoC7 encounter not found: {encounter_id}")
        result = row_to_dict(row)
        result["state"] = decode_json_field(result.pop("state_json"), {})
        return result

    def list_coc7_encounters(
        self, campaign_id: str, session_id: str
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id FROM coc7_encounters
            WHERE campaign_id = ? AND session_id = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (campaign_id, session_id),
        ).fetchall()
        return [self.get_coc7_encounter(str(row["id"])) for row in rows]

    def find_coc7_gameplay_event(
        self, session_id: str, command_id: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM coc7_gameplay_events
            WHERE session_id = ? AND command_id = ?
            """,
            (session_id, command_id),
        ).fetchone()
        return self._decode_gameplay_event(row) if row is not None else None

    def list_coc7_gameplay_events(
        self,
        *,
        encounter_id: str | None = None,
        campaign_id: str | None = None,
        investigator_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if encounter_id:
            rows = self.connection.execute(
                """
                SELECT * FROM coc7_gameplay_events
                WHERE encounter_id = ?
                ORDER BY aggregate_version, created_at, id
                """,
                (encounter_id,),
            ).fetchall()
        elif campaign_id and investigator_id:
            rows = self.connection.execute(
                """
                SELECT * FROM coc7_gameplay_events
                WHERE campaign_id = ? AND investigator_id = ?
                ORDER BY created_at, id
                """,
                (campaign_id, investigator_id),
            ).fetchall()
        else:
            raise ValueError("Event listing requires an encounter or investigator scope")
        return [self._decode_gameplay_event(row) for row in rows]

    def transition_coc7_encounter(
        self,
        encounter_id: str,
        *,
        expected_version: int,
        state: dict[str, Any],
        round_no: int,
        turn_index: int,
        status: str,
        member_id: str,
        command_id: str,
        event_type: str,
        command_input: dict[str, Any],
        result: dict[str, Any],
        visibility: str,
        ruleset_id: str,
        ruleset_version: str,
        source_reference: dict[str, Any],
    ) -> dict[str, Any]:
        self.begin_immediate()
        encounter = self.get_coc7_encounter(encounter_id)
        existing = self.find_coc7_gameplay_event(
            str(encounter["session_id"]), command_id
        )
        if existing is not None:
            if existing["encounter_id"] != encounter_id:
                raise ValueError("Command ID was already used for another aggregate")
            return {
                "encounter": encounter,
                "event": existing,
                "idempotent_replay": True,
            }
        if encounter["version"] != expected_version:
            raise ValueError("CoC7 encounter changed; refresh and retry")
        next_version = expected_version + 1
        updated = self.connection.execute(
            """
            UPDATE coc7_encounters
            SET state_json = ?, round_no = ?, turn_index = ?, status = ?,
                version = ?, updated_at = CURRENT_TIMESTAMP,
                completed_at = CASE
                  WHEN ? IN ('completed', 'cancelled') THEN CURRENT_TIMESTAMP
                  ELSE NULL
                END
            WHERE id = ? AND version = ?
            """,
            (
                json.dumps(state, ensure_ascii=False, sort_keys=True),
                round_no,
                turn_index,
                status,
                next_version,
                status,
                encounter_id,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("CoC7 encounter changed; refresh and retry")
        event = self._insert_gameplay_event(
            campaign_id=str(encounter["campaign_id"]),
            session_id=str(encounter["session_id"]),
            encounter_id=encounter_id,
            investigator_id=None,
            event_type=event_type,
            member_id=member_id,
            command_id=command_id,
            visibility=visibility,
            command_input=command_input,
            result=result,
            aggregate_version=next_version,
            ruleset_id=ruleset_id,
            ruleset_version=ruleset_version,
            source_reference=source_reference,
        )
        return {
            "encounter": self.get_coc7_encounter(encounter_id),
            "event": event,
            "idempotent_replay": False,
        }

    def transition_coc7_character_state(
        self,
        *,
        campaign_id: str,
        session_id: str,
        investigator_id: str,
        expected_version: int,
        changes: dict[str, Any],
        member_id: str,
        command_id: str,
        event_type: str,
        command_input: dict[str, Any],
        result: dict[str, Any],
        visibility: str,
        ruleset_id: str,
        ruleset_version: str,
        source_reference: dict[str, Any],
    ) -> dict[str, Any]:
        self.begin_immediate()
        existing = self.find_coc7_gameplay_event(session_id, command_id)
        if existing is not None:
            if (
                existing["campaign_id"] != campaign_id
                or existing["investigator_id"] != investigator_id
            ):
                raise ValueError("Command ID was already used for another aggregate")
            record = self.get_campaign_investigator(campaign_id, investigator_id)
            return {
                "state": record["campaign_state"],
                "event": existing,
                "idempotent_replay": True,
            }
        state = self.update_investigator_campaign_state(
            campaign_id=campaign_id,
            investigator_id=investigator_id,
            expected_version=expected_version,
            changes=changes,
        )
        event = self._insert_gameplay_event(
            campaign_id=campaign_id,
            session_id=session_id,
            encounter_id=None,
            investigator_id=investigator_id,
            event_type=event_type,
            member_id=member_id,
            command_id=command_id,
            visibility=visibility,
            command_input=command_input,
            result=result,
            aggregate_version=int(state["state_version"]),
            ruleset_id=ruleset_id,
            ruleset_version=ruleset_version,
            source_reference=source_reference,
        )
        return {
            "state": state,
            "event": event,
            "idempotent_replay": False,
        }

    def _insert_gameplay_event(self, **values: Any) -> dict[str, Any]:
        event_id = new_id("gameplay_event")
        self.connection.execute(
            """
            INSERT INTO coc7_gameplay_events
              (id, campaign_id, session_id, encounter_id, investigator_id,
               event_type, actor_member_id, command_id, visibility,
               input_json, result_json, aggregate_version, ruleset_id,
               ruleset_version, source_reference_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                values["campaign_id"],
                values["session_id"],
                values["encounter_id"],
                values["investigator_id"],
                values["event_type"],
                values["member_id"],
                values["command_id"],
                values["visibility"],
                json.dumps(values["command_input"], ensure_ascii=False, sort_keys=True),
                json.dumps(values["result"], ensure_ascii=False, sort_keys=True),
                values["aggregate_version"],
                values["ruleset_id"],
                values["ruleset_version"],
                json.dumps(
                    values["source_reference"], ensure_ascii=False, sort_keys=True
                ),
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM coc7_gameplay_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        return self._decode_gameplay_event(row)

    @staticmethod
    def _decode_gameplay_event(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        result["input"] = decode_json_field(result.pop("input_json"), {})
        result["result"] = decode_json_field(result.pop("result_json"), {})
        result["source_reference"] = decode_json_field(
            result.pop("source_reference_json"), {}
        )
        return result


__all__ = ["GameplayRepository"]
