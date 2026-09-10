"""Authoritative campaign objective persistence and role-safe projection."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class CampaignObjectiveRepository(SQLiteRepository):
    def create_campaign_objective(
        self,
        *,
        campaign_id: str,
        command_id: str,
        title: str,
        public_description: str,
        kp_notes: str,
        visibility: str,
        source_refs: list[dict[str, Any]],
        actor_member_id: str,
    ) -> dict[str, Any]:
        replay = self._event_by_command(campaign_id, command_id)
        if replay is not None:
            return self.get_campaign_objective(str(replay["objective_id"]))
        objective_id = new_id("objective")
        self.connection.execute(
            """
            INSERT INTO campaign_objectives
              (id, campaign_id, title, public_description, kp_notes, visibility,
               source_refs_json, created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                objective_id,
                campaign_id,
                title,
                public_description,
                kp_notes,
                visibility,
                json.dumps(source_refs, ensure_ascii=False, sort_keys=True),
                actor_member_id,
            ),
        )
        self._append_event(
            campaign_id=campaign_id,
            objective_id=objective_id,
            command_id=command_id,
            from_status=None,
            to_status="open",
            public_progress="",
            kp_notes=kp_notes,
            actor_member_id=actor_member_id,
            source_refs=source_refs,
        )
        return self.get_campaign_objective(objective_id)

    def update_campaign_objective(
        self,
        objective_id: str,
        *,
        command_id: str,
        expected_version: int,
        status: str,
        public_progress: str,
        kp_notes: str,
        source_refs: list[dict[str, Any]],
        actor_member_id: str,
    ) -> dict[str, Any]:
        objective = self.get_campaign_objective(objective_id)
        replay = self._event_by_command(str(objective["campaign_id"]), command_id)
        if replay is not None:
            if str(replay["objective_id"]) != objective_id:
                raise ValueError("Objective command id belongs to another objective")
            return objective
        updated = self.connection.execute(
            """
            UPDATE campaign_objectives
            SET status = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ?
            """,
            (status, objective_id, expected_version),
        )
        if updated.rowcount != 1:
            raise ValueError("Objective changed; refresh before updating it")
        self._append_event(
            campaign_id=str(objective["campaign_id"]),
            objective_id=objective_id,
            command_id=command_id,
            from_status=str(objective["status"]),
            to_status=status,
            public_progress=public_progress,
            kp_notes=kp_notes,
            actor_member_id=actor_member_id,
            source_refs=source_refs,
        )
        return self.get_campaign_objective(objective_id)

    def get_campaign_objective(self, objective_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM campaign_objectives WHERE id = ?", (objective_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Campaign objective not found: {objective_id}")
        result = self._decode_campaign_objective(row)
        result["events"] = self.list_campaign_objective_events(objective_id)
        return result

    def list_campaign_objectives(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM campaign_objectives
            WHERE campaign_id = ?
            ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'blocked' THEN 1 ELSE 2 END,
                     updated_at DESC, id
            """,
            (campaign_id,),
        ).fetchall()
        event_rows = self.connection.execute(
            """
            SELECT * FROM campaign_objective_events
            WHERE campaign_id = ? ORDER BY created_at, id
            """,
            (campaign_id,),
        ).fetchall()
        events_by_objective: dict[str, list[dict[str, Any]]] = {}
        for row in event_rows:
            event = self._decode_campaign_objective_event(row)
            events_by_objective.setdefault(str(event["objective_id"]), []).append(event)
        objectives = []
        for row in rows:
            objective = self._decode_campaign_objective(row)
            objective["events"] = events_by_objective.get(str(objective["id"]), [])
            objectives.append(objective)
        return objectives

    def list_campaign_objective_events(self, objective_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM campaign_objective_events
            WHERE objective_id = ? ORDER BY created_at, id
            """,
            (objective_id,),
        ).fetchall()
        return [self._decode_campaign_objective_event(row) for row in rows]

    def _event_by_command(self, campaign_id: str, command_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM campaign_objective_events
            WHERE campaign_id = ? AND command_id = ?
            """,
            (campaign_id, command_id),
        ).fetchone()
        return row_to_dict(row) if row is not None else None

    def _append_event(self, **values: Any) -> None:
        self.connection.execute(
            """
            INSERT INTO campaign_objective_events
              (id, campaign_id, objective_id, command_id, from_status, to_status,
               public_progress, kp_notes, actor_member_id, source_refs_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("objective_event"),
                values["campaign_id"],
                values["objective_id"],
                values["command_id"],
                values["from_status"],
                values["to_status"],
                values["public_progress"],
                values["kp_notes"],
                values["actor_member_id"],
                json.dumps(values["source_refs"], ensure_ascii=False, sort_keys=True),
            ),
        )

    @staticmethod
    def _decode_campaign_objective(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        result["source_refs"] = decode_json_field(result.pop("source_refs_json"), [])
        return result

    @staticmethod
    def _decode_campaign_objective_event(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        result["source_refs"] = decode_json_field(result.pop("source_refs_json"), [])
        return result


__all__ = ["CampaignObjectiveRepository"]
