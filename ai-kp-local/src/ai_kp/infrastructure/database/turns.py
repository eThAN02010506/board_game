"""SQLite adapter for player actions, KP proposals, approval, and audit."""

import json
import sqlite3

from ai_kp.core.ids import new_id
from ai_kp.director.turn_output import (
    CheckCandidate,
    EventCandidate,
    MapMoveCandidate,
    MemoryCandidate,
    NpcUpdateCandidate,
    dump_candidates,
)
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


PROPOSAL_JSON_FIELDS = (
    "proposed_checks",
    "proposed_events",
    "proposed_memories",
    "proposed_npc_updates",
    "proposed_map_moves",
)


class TurnRepository(SQLiteRepository):
    """Persistence and atomic application of KP turn proposals."""

    def create_turn_proposal(
        self,
        campaign_id: str,
        player_action: str,
        public_narration: str,
        pc_id: str | None = None,
        kp_notes: str = "",
        proposed_checks: list[dict] | None = None,
        proposed_events: list[dict] | None = None,
        proposed_memories: list[dict] | None = None,
        proposed_npc_updates: list[dict] | None = None,
        proposed_map_moves: list[dict] | None = None,
        source_model: str = "unknown",
    ) -> dict:
        if pc_id:
            self._require_pc_in_campaign(pc_id, campaign_id)
        checks = dump_candidates(proposed_checks or [], CheckCandidate)
        events = dump_candidates(proposed_events or [], EventCandidate)
        memories = dump_candidates(proposed_memories or [], MemoryCandidate)
        npc_updates = dump_candidates(proposed_npc_updates or [], NpcUpdateCandidate)
        map_moves = dump_candidates(proposed_map_moves or [], MapMoveCandidate)
        proposal_id = new_id("proposal")
        self.connection.execute(
            """
            INSERT INTO turn_proposals
              (
                id, campaign_id, pc_id, status, player_action, public_narration, kp_notes,
                proposed_checks_json, proposed_events_json, proposed_memories_json,
                proposed_npc_updates_json, proposed_map_moves_json, source_model
              )
            VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal_id,
                campaign_id,
                pc_id,
                player_action,
                public_narration,
                kp_notes,
                json.dumps(checks, ensure_ascii=False),
                json.dumps(events, ensure_ascii=False),
                json.dumps(memories, ensure_ascii=False),
                json.dumps(npc_updates, ensure_ascii=False),
                json.dumps(map_moves, ensure_ascii=False),
                source_model,
            ),
        )
        self.add_proposal_action(proposal_id, "created", actor="ai", note="proposal created")
        return self.get_turn_proposal(proposal_id)

    def get_turn_proposal(self, proposal_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM turn_proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Turn proposal not found: {proposal_id}")
        result = self._decode_proposal(row)
        result["actions"] = self.list_proposal_actions(proposal_id)
        return result

    def list_turn_proposals(self, campaign_id: str, status: str | None = None) -> list[dict]:
        if status:
            rows = self.connection.execute(
                """
                SELECT * FROM turn_proposals
                WHERE campaign_id = ? AND status = ?
                ORDER BY created_at DESC
                """,
                (campaign_id, status),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT * FROM turn_proposals
                WHERE campaign_id = ?
                ORDER BY created_at DESC
                """,
                (campaign_id,),
            ).fetchall()
        return [self._decode_proposal(row) for row in rows]

    @staticmethod
    def _decode_proposal(row: sqlite3.Row) -> dict:
        proposal = row_to_dict(row)
        for field_name in PROPOSAL_JSON_FIELDS:
            proposal[field_name] = decode_json_field(
                proposal.pop(f"{field_name}_json"),
                [],
            )
        return proposal

    def add_proposal_action(
        self,
        proposal_id: str,
        action_type: str,
        actor: str = "human_kp",
        note: str = "",
        payload: dict | None = None,
    ) -> dict:
        action_id = new_id("pa")
        self.connection.execute(
            """
            INSERT INTO proposal_actions (id, proposal_id, action_type, actor, note, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                proposal_id,
                action_type,
                actor,
                note,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )
        return row_to_dict(
            self.connection.execute(
                "SELECT * FROM proposal_actions WHERE id = ?",
                (action_id,),
            ).fetchone()
        )

    def list_proposal_actions(self, proposal_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM proposal_actions
            WHERE proposal_id = ?
            ORDER BY created_at ASC
            """,
            (proposal_id,),
        ).fetchall()
        actions = []
        for row in rows:
            action = row_to_dict(row)
            action["payload"] = decode_json_field(action.pop("payload_json"), {})
            actions.append(action)
        return actions

    def approve_turn_proposal(
        self,
        proposal_id: str,
        actor: str = "human_kp",
        note: str = "",
        override_public_narration: str | None = None,
    ) -> dict:
        self.connection.execute("SAVEPOINT approve_turn_proposal")
        try:
            claimed = self.connection.execute(
                """
                UPDATE turn_proposals
                SET status = 'applying'
                WHERE id = ? AND status = 'draft'
                """,
                (proposal_id,),
            )
            if claimed.rowcount != 1:
                proposal = self.get_turn_proposal(proposal_id)
                raise ValueError(
                    "Only draft proposals can be approved; "
                    f"current status is {proposal['status']}"
                )
            result = self._apply_turn_proposal(
                proposal_id,
                actor=actor,
                note=note,
                override_public_narration=override_public_narration,
            )
            self.connection.execute("RELEASE SAVEPOINT approve_turn_proposal")
            return result
        except Exception:
            self.connection.execute("ROLLBACK TO SAVEPOINT approve_turn_proposal")
            self.connection.execute("RELEASE SAVEPOINT approve_turn_proposal")
            raise

    def _apply_turn_proposal(
        self,
        proposal_id: str,
        actor: str,
        note: str,
        override_public_narration: str | None,
    ) -> dict:
        proposal = self.get_turn_proposal(proposal_id)
        public_narration = override_public_narration or proposal["public_narration"]
        if override_public_narration:
            self.connection.execute(
                """
                UPDATE turn_proposals
                SET public_narration = ?, status = 'overridden'
                WHERE id = ?
                """,
                (override_public_narration, proposal_id),
            )
            self.add_proposal_action(
                proposal_id,
                "overridden",
                actor=actor,
                note=note,
                payload={"public_narration": override_public_narration},
            )
        event_payload = {
            "proposal_id": proposal_id,
            "player_action": proposal["player_action"],
        }
        applied_event = self.append_event(
            campaign_id=proposal["campaign_id"],
            actor_type="kp",
            actor_id=actor,
            visibility="table",
            event_type="kp_turn",
            summary=public_narration,
            payload=event_payload,
        )
        for proposed_check in proposal["proposed_checks"]:
            check_pc_id = proposed_check.get("pc_id") or proposal["pc_id"]
            if check_pc_id:
                self._require_pc_in_campaign(check_pc_id, proposal["campaign_id"])
            self.append_event(
                campaign_id=proposal["campaign_id"],
                actor_type="pc",
                actor_id=check_pc_id,
                visibility="kp" if proposed_check.get("hidden") else "table",
                event_type="check_requested",
                summary=(
                    f"{proposed_check['skill']} {proposed_check['difficulty']} check: "
                    f"{proposed_check['reason']}"
                ),
                payload=proposed_check,
            )
        for proposed_event in proposal["proposed_events"]:
            if proposed_event.get("actor_id") and proposed_event["actor_type"] == "pc":
                self._require_pc_in_campaign(
                    proposed_event["actor_id"], proposal["campaign_id"]
                )
            if proposed_event.get("actor_id") and proposed_event["actor_type"] == "npc":
                self._require_npc_in_campaign(
                    proposed_event["actor_id"], proposal["campaign_id"]
                )
            self.append_event(
                campaign_id=proposal["campaign_id"],
                actor_type=proposed_event.get("actor_type", "system"),
                actor_id=proposed_event.get("actor_id"),
                visibility=proposed_event.get("visibility", "table"),
                event_type=proposed_event.get("event_type", "proposed_event"),
                happened_at=proposed_event.get("happened_at"),
                summary=proposed_event.get("summary", ""),
                payload=proposed_event.get("payload", {}),
            )
        for proposed_memory in proposal["proposed_memories"]:
            memory_pc_id = proposed_memory.get("pc_id") or proposal["pc_id"]
            if memory_pc_id:
                self._require_pc_in_campaign(memory_pc_id, proposal["campaign_id"])
            if proposed_memory.get("npc_id"):
                self._require_npc_in_campaign(
                    proposed_memory["npc_id"], proposal["campaign_id"]
                )
            self.add_memory(
                text=proposed_memory.get("text", ""),
                scope=proposed_memory.get("scope", "campaign_fact"),
                campaign_id=proposal["campaign_id"],
                pc_id=memory_pc_id,
                npc_id=proposed_memory.get("npc_id"),
                importance=int(proposed_memory.get("importance", 1)),
                visibility=proposed_memory.get("visibility", "table"),
                happened_at=proposed_memory.get("happened_at"),
                source_event_id=applied_event["id"],
            )
        for proposed_npc_update in proposal["proposed_npc_updates"]:
            self._apply_npc_update(proposal["campaign_id"], proposed_npc_update)
            self.append_event(
                campaign_id=proposal["campaign_id"],
                actor_type="npc",
                actor_id=proposed_npc_update["npc_id"],
                visibility="kp",
                event_type="npc_updated",
                summary=(
                    proposed_npc_update.get("note")
                    or "NPC relationship changed by "
                    f"{proposed_npc_update['relationship_delta']}"
                ),
                payload={
                    "appeared": proposed_npc_update.get("appeared", False),
                    "relationship_delta": proposed_npc_update.get(
                        "relationship_delta", 0
                    ),
                    "last_seen_time": proposed_npc_update.get("last_seen_time"),
                },
            )
        for proposed_map_move in proposal["proposed_map_moves"]:
            token = self._require_token_in_campaign(
                proposed_map_move["token_id"], proposal["campaign_id"]
            )
            moved_token = self.move_map_token(
                token_id=proposed_map_move["token_id"],
                to_location_name=proposed_map_move["to_location_name"],
                moved_by=actor,
                note=f"approved proposal {proposal_id}",
                require_route=proposed_map_move.get("require_route", True),
            )
            self.append_event(
                campaign_id=proposal["campaign_id"],
                actor_type=token["actor_type"],
                actor_id=token["actor_id"],
                visibility=token["visibility"],
                event_type="map_token_moved",
                summary=(
                    f"{token['label']} moved from {token['location_name']} "
                    f"to {moved_token['location_name']}"
                ),
                payload={
                    "token_id": token["id"],
                    "map_id": token["map_id"],
                    "from_location": token["location_name"],
                    "to_location": moved_token["location_name"],
                },
            )
        self.connection.execute(
            """
            UPDATE turn_proposals
            SET status = 'approved', applied_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (proposal_id,),
        )
        self.add_proposal_action(
            proposal_id,
            "approved",
            actor=actor,
            note=note,
            payload={"applied_event_id": applied_event["id"]},
        )
        if not proposal["proposed_checks"]:
            self.resolve_player_action_for_proposal(proposal_id, "resolved")
        return self.get_turn_proposal(proposal_id)

    def _require_token_in_campaign(self, token_id: str, campaign_id: str) -> dict:
        row = self.connection.execute(
            """
            SELECT t.*, l.name AS location_name
            FROM map_tokens t
            JOIN maps m ON m.id = t.map_id
            JOIN map_locations l ON l.id = t.location_id
            WHERE t.id = ? AND m.campaign_id = ?
            """,
            (token_id, campaign_id),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"Map token {token_id} does not belong to campaign {campaign_id}"
            )
        return row_to_dict(row)

    def reject_turn_proposal(
        self,
        proposal_id: str,
        actor: str = "human_kp",
        note: str = "",
    ) -> dict:
        self.connection.execute("SAVEPOINT reject_turn_proposal")
        try:
            rejected = self.connection.execute(
                """
                UPDATE turn_proposals
                SET status = 'rejected'
                WHERE id = ? AND status = 'draft'
                """,
                (proposal_id,),
            )
            if rejected.rowcount != 1:
                proposal = self.get_turn_proposal(proposal_id)
                raise ValueError(
                    "Only draft proposals can be rejected; "
                    f"current status is {proposal['status']}"
                )
            self.add_proposal_action(proposal_id, "rejected", actor=actor, note=note)
            self.resolve_player_action_for_proposal(proposal_id, "rejected")
            result = self.get_turn_proposal(proposal_id)
            self.connection.execute("RELEASE SAVEPOINT reject_turn_proposal")
            return result
        except Exception:
            self.connection.execute("ROLLBACK TO SAVEPOINT reject_turn_proposal")
            self.connection.execute("RELEASE SAVEPOINT reject_turn_proposal")
            raise
