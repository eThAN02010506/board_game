"""Persistence for AI rulings that require explicit player confirmation."""

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class ActionAdjudicationRepository(SQLiteRepository):
    def create_action_adjudication(
        self,
        *,
        action_id: str,
        proposal_id: str,
        mode: str,
        reason: str,
        prompt: str,
        skill_options: list[dict[str, Any]],
        selected_skill: str | None,
        source_model: str,
        source_error: str | None = None,
    ) -> dict[str, Any]:
        adjudication_id = new_id("adjudication")
        self.connection.execute(
            """
            INSERT INTO player_action_adjudications
              (id, action_id, proposal_id, mode, reason, prompt,
               skill_options_json, selected_skill, source_model, source_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                adjudication_id,
                action_id,
                proposal_id,
                mode,
                reason,
                prompt,
                json.dumps(skill_options, ensure_ascii=False),
                selected_skill,
                source_model,
                source_error,
            ),
        )
        self._append_adjudication_event(adjudication_id, 1, "created", None, {})
        return self.get_action_adjudication(action_id)

    def get_action_adjudication(self, action_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM player_action_adjudications WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Action adjudication not found: {action_id}")
        result = dict(row)
        result["skill_options"] = decode_json_field(
            result.pop("skill_options_json"), []
        )
        events = self.connection.execute(
            """
            SELECT * FROM player_action_adjudication_events
            WHERE adjudication_id = ? ORDER BY created_at, id
            """,
            (result["id"],),
        ).fetchall()
        result["events"] = [
            {**dict(item), "payload": decode_json_field(item["payload_json"], {})}
            for item in events
        ]
        for item in result["events"]:
            item.pop("payload_json", None)
        proposal = self.get_turn_proposal(str(result["proposal_id"]))
        result["ruling"] = proposal.get("action_ruling") or {}
        return result

    def list_proposal_adjudications(self, proposal_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT action_id FROM player_action_adjudications
            WHERE proposal_id = ? ORDER BY created_at, id
            """,
            (proposal_id,),
        ).fetchall()
        return [self.get_action_adjudication(str(row["action_id"])) for row in rows]

    def list_member_pending_adjudications(
        self, campaign_id: str, member_id: str
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT a.action_id FROM player_action_adjudications a
            JOIN player_actions p ON p.id = a.action_id
            WHERE p.campaign_id = ? AND p.member_id = ? AND a.status = 'pending'
            ORDER BY a.updated_at DESC, a.id DESC
            """,
            (campaign_id, member_id),
        ).fetchall()
        return [self.get_action_adjudication(str(row["action_id"])) for row in rows]

    def replace_adjudication_skill(
        self,
        action_id: str,
        *,
        expected_version: int,
        skill_name: str,
        actor_member_id: str,
    ) -> dict[str, Any]:
        current = self.get_action_adjudication(action_id)
        if current["status"] != "pending" or current["mode"] != "skill_check":
            raise ValueError("Only a pending skill-check ruling can change skill")
        option = next(
            (item for item in current["skill_options"] if item["skill_name"] == skill_name),
            None,
        )
        if option is None:
            raise ValueError("Selected skill is not an approved character-sheet option")
        updated = self.connection.execute(
            """
            UPDATE player_action_adjudications
            SET selected_skill = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE action_id = ? AND version = ? AND status = 'pending'
            """,
            (skill_name, action_id, expected_version),
        )
        if updated.rowcount != 1:
            raise ValueError("The ruling changed; refresh before choosing a skill")
        proposal = self.get_turn_proposal(str(current["proposal_id"]))
        checks = list(proposal["proposed_checks"])
        checks[0] = {
            **checks[0],
            "skill": skill_name,
            "reason": str(option["reason"]),
            "difficulty": str(option["difficulty"]),
            "hidden": bool(option["hidden"]),
        }
        self.connection.execute(
            "UPDATE turn_proposals SET proposed_checks_json = ? WHERE id = ? AND status = 'draft'",
            (json.dumps(checks, ensure_ascii=False), current["proposal_id"]),
        )
        self._append_adjudication_event(
            str(current["id"]), expected_version + 1, "skill_changed",
            actor_member_id, {"skill_name": skill_name},
        )
        return self.get_action_adjudication(action_id)

    def confirm_action_adjudication(
        self,
        action_id: str,
        *,
        expected_version: int,
        actor_member_id: str,
    ) -> dict[str, Any]:
        current = self.get_action_adjudication(action_id)
        if current["mode"] == "roleplay_or_clarification":
            raise ValueError("This ruling needs a revised action or roleplay response")
        updated = self.connection.execute(
            """
            UPDATE player_action_adjudications
            SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE action_id = ? AND version = ? AND status = 'pending'
            """,
            (action_id, expected_version),
        )
        if updated.rowcount != 1:
            raise ValueError("The ruling changed or was already confirmed")
        self._append_adjudication_event(
            str(current["id"]), expected_version, "confirmed", actor_member_id, {}
        )
        return self.get_action_adjudication(action_id)

    def revise_player_action_for_adjudication(
        self,
        action_id: str,
        *,
        expected_version: int,
        actor_member_id: str,
    ) -> dict[str, Any]:
        current = self.get_action_adjudication(action_id)
        updated = self.connection.execute(
            """
            UPDATE player_action_adjudications
            SET status = 'superseded', updated_at = CURRENT_TIMESTAMP
            WHERE action_id = ? AND version = ? AND status = 'pending'
            """,
            (action_id, expected_version),
        )
        if updated.rowcount != 1:
            raise ValueError("The ruling changed; refresh before revising")
        self.connection.execute(
            "UPDATE turn_proposals SET status = 'rejected' WHERE id = ? AND status = 'draft'",
            (current["proposal_id"],),
        )
        self.connection.execute(
            """
            UPDATE player_actions SET status = 'rejected', resolved_at = CURRENT_TIMESTAMP
            WHERE id = ? AND member_id = ? AND status = 'reviewed'
            """,
            (action_id, actor_member_id),
        )
        self._append_adjudication_event(
            str(current["id"]), expected_version, "superseded", actor_member_id,
            {},
        )
        return self.get_player_action(action_id)

    def _append_adjudication_event(
        self,
        adjudication_id: str,
        version: int,
        event_type: str,
        actor_member_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO player_action_adjudication_events
              (id, adjudication_id, version, event_type, actor_member_id, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("adjudicationevent"), adjudication_id, version, event_type,
                actor_member_id, json.dumps(payload, ensure_ascii=False),
            ),
        )
