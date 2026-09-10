"""SQLite persistence for restart-safe character lifecycle decisions."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class CharacterLifecycleRepository(SQLiteRepository):
    def ensure_investigator_lifecycle(
        self, campaign_id: str, investigator_id: str
    ) -> dict[str, Any]:
        self.connection.execute(
            """
            INSERT INTO campaign_investigator_lifecycle
              (campaign_id, investigator_id)
            SELECT ?, ?
            WHERE EXISTS (
              SELECT 1 FROM campaign_investigators
              WHERE campaign_id = ? AND investigator_id = ?
                AND approved_revision_id IS NOT NULL
            )
            ON CONFLICT(campaign_id, investigator_id) DO NOTHING
            """,
            (campaign_id, investigator_id, campaign_id, investigator_id),
        )
        return self.get_investigator_lifecycle(campaign_id, investigator_id)

    def get_investigator_lifecycle(
        self, campaign_id: str, investigator_id: str
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT * FROM campaign_investigator_lifecycle
            WHERE campaign_id = ? AND investigator_id = ?
            """,
            (campaign_id, investigator_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"Investigator lifecycle not found: {investigator_id}")
        return row_to_dict(row)

    def list_investigator_lifecycles(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT ci.campaign_id, ci.investigator_id,
                   COALESCE(cil.state, 'active') AS state,
                   COALESCE(cil.version, 1) AS version,
                   cil.last_source_event_id,
                   COALESCE(cil.updated_at, ci.updated_at) AS updated_at,
                   pc.name AS investigator_name
            FROM campaign_investigators ci
            LEFT JOIN campaign_investigator_lifecycle cil
              ON cil.campaign_id = ci.campaign_id
             AND cil.investigator_id = ci.investigator_id
            LEFT JOIN player_characters pc ON pc.id = ci.legacy_pc_id
            WHERE ci.campaign_id = ? AND ci.approved_revision_id IS NOT NULL
            ORDER BY COALESCE(cil.updated_at, ci.updated_at), ci.investigator_id
            """,
            (campaign_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def transition_investigator_lifecycle(
        self,
        campaign_id: str,
        investigator_id: str,
        *,
        expected_version: int,
        to_state: str,
        source_event_id: str | None,
    ) -> dict[str, Any]:
        updated = self.connection.execute(
            """
            UPDATE campaign_investigator_lifecycle
            SET state = ?, last_source_event_id = ?, version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE campaign_id = ? AND investigator_id = ? AND version = ?
            """,
            (
                to_state,
                source_event_id,
                campaign_id,
                investigator_id,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Character lifecycle changed; refresh and retry")
        return self.get_investigator_lifecycle(campaign_id, investigator_id)

    def ensure_member_presence(
        self,
        *,
        campaign_id: str,
        session_id: str,
        member_id: str,
        investigator_id: str | None,
        state: str = "active",
    ) -> dict[str, Any]:
        self.connection.execute(
            """
            INSERT INTO campaign_member_presence
              (campaign_id, session_id, member_id, state, investigator_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, member_id) DO NOTHING
            """,
            (campaign_id, session_id, member_id, state, investigator_id),
        )
        return self.get_member_presence(campaign_id, member_id)

    def list_member_presence(
        self, campaign_id: str, session_id: str
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT sm.id AS member_id, sm.display_name,
                   COALESCE(cmp.state,
                     CASE WHEN sm.role = 'player' THEN 'active' ELSE 'observing' END
                   ) AS state,
                   COALESCE(cmp.investigator_id, ci.investigator_id) AS investigator_id,
                   COALESCE(cmp.version, 1) AS version
            FROM session_members sm
            LEFT JOIN campaign_member_presence cmp
              ON cmp.campaign_id = sm.campaign_id AND cmp.member_id = sm.id
            LEFT JOIN campaign_investigators ci
              ON ci.campaign_id = sm.campaign_id AND ci.legacy_pc_id = sm.pc_id
            WHERE sm.campaign_id = ? AND sm.session_id = ?
              AND sm.role IN ('player', 'observer') AND sm.revoked_at IS NULL
            ORDER BY sm.joined_at, sm.id
            """,
            (campaign_id, session_id),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def get_member_presence(self, campaign_id: str, member_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT * FROM campaign_member_presence
            WHERE campaign_id = ? AND member_id = ?
            """,
            (campaign_id, member_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"Member lifecycle not found: {member_id}")
        return row_to_dict(row)

    def transition_member_presence(
        self,
        campaign_id: str,
        member_id: str,
        *,
        expected_version: int,
        state: str,
        investigator_id: str | None,
    ) -> dict[str, Any]:
        updated = self.connection.execute(
            """
            UPDATE campaign_member_presence
            SET state = ?, investigator_id = ?, version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE campaign_id = ? AND member_id = ? AND version = ?
            """,
            (state, investigator_id, campaign_id, member_id, expected_version),
        )
        if updated.rowcount != 1:
            raise ValueError("Member lifecycle changed; refresh and retry")
        return self.get_member_presence(campaign_id, member_id)

    def create_lifecycle_request(self, **values: Any) -> dict[str, Any]:
        request_id = str(values.get("id") or new_id("life"))
        self.connection.execute(
            """
            INSERT INTO character_lifecycle_requests
              (id, campaign_id, session_id, member_id, investigator_id,
               replacement_investigator_id, action, reason,
               base_lifecycle_version, proposed_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                values["campaign_id"],
                values["session_id"],
                values["member_id"],
                values.get("investigator_id"),
                values.get("replacement_investigator_id"),
                values["action"],
                values["reason"],
                values.get("base_lifecycle_version"),
                values["proposed_by_member_id"],
            ),
        )
        return self.get_lifecycle_request(request_id)

    def get_lifecycle_request(self, request_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM character_lifecycle_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Character lifecycle request not found: {request_id}")
        return row_to_dict(row)

    def list_lifecycle_requests(
        self, campaign_id: str, *, member_id: str | None = None
    ) -> list[dict[str, Any]]:
        clause = " AND member_id = ?" if member_id else ""
        params: tuple[Any, ...] = (
            (campaign_id, member_id) if member_id else (campaign_id,)
        )
        rows = self.connection.execute(
            f"""
            SELECT * FROM character_lifecycle_requests
            WHERE campaign_id = ?{clause}
            ORDER BY created_at DESC, id DESC
            """,
            params,
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def decide_lifecycle_request(
        self,
        request_id: str,
        *,
        expected_version: int,
        status: str,
        decided_by_member_id: str,
    ) -> dict[str, Any]:
        updated = self.connection.execute(
            """
            UPDATE character_lifecycle_requests
            SET status = ?, decided_by_member_id = ?, version = version + 1,
                updated_at = CURRENT_TIMESTAMP, decided_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'awaiting_player' AND version = ?
            """,
            (status, decided_by_member_id, request_id, expected_version),
        )
        if updated.rowcount != 1:
            raise ValueError("Lifecycle request changed; refresh and retry")
        return self.get_lifecycle_request(request_id)

    def set_member_control(
        self, member_id: str, *, role: str, pc_id: str | None
    ) -> dict[str, Any]:
        updated = self.connection.execute(
            """
            UPDATE session_members SET role = ?, pc_id = ?
            WHERE id = ? AND revoked_at IS NULL AND role IN ('player', 'observer')
            """,
            (role, pc_id, member_id),
        )
        if updated.rowcount != 1:
            raise ValueError("Member control changed; refresh and retry")
        self.connection.execute(
            """
            UPDATE session_seats SET assigned_pc_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE claimed_member_id = ? AND status = 'claimed'
            """,
            (pc_id, member_id),
        )
        return self.get_session_member(member_id)

    def find_investigator_by_pc(self, campaign_id: str, pc_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT investigator_id, legacy_pc_id, owner_profile_id, approved_revision_id
            FROM campaign_investigators
            WHERE campaign_id = ? AND legacy_pc_id = ?
            """,
            (campaign_id, pc_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"Campaign investigator not found for character: {pc_id}")
        return row_to_dict(row)

    def append_lifecycle_event(self, **values: Any) -> dict[str, Any]:
        event_id = str(values.get("id") or new_id("life_evt"))
        self.connection.execute(
            """
            INSERT INTO character_lifecycle_events
              (id, campaign_id, session_id, investigator_id, member_id,
               command_id, action, from_state, to_state, reason, source_event_id,
               actor_member_id, public_summary, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, command_id) DO NOTHING
            """,
            (
                event_id,
                values["campaign_id"],
                values.get("session_id"),
                values.get("investigator_id"),
                values.get("member_id"),
                values["command_id"],
                values["action"],
                values.get("from_state"),
                values["to_state"],
                values["reason"],
                values.get("source_event_id"),
                values.get("actor_member_id"),
                values["public_summary"],
                json.dumps(values.get("details", {}), ensure_ascii=False, sort_keys=True),
            ),
        )
        row = self.connection.execute(
            """
            SELECT * FROM character_lifecycle_events
            WHERE campaign_id = ? AND command_id = ?
            """,
            (values["campaign_id"], values["command_id"]),
        ).fetchone()
        assert row is not None
        result = row_to_dict(row)
        result["details"] = json.loads(result.pop("details_json"))
        return result

    def list_lifecycle_events(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT rowid AS sequence, * FROM character_lifecycle_events
            WHERE campaign_id = ? ORDER BY rowid
            """,
            (campaign_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = row_to_dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result


__all__ = ["CharacterLifecycleRepository"]
