"""SQLite adapter for sessions, members, authentication, and revocation state."""

import sqlite3

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.player_action_authority import PlayerActionAuthority
from ai_kp.infrastructure.security.tokens import (
    generate_access_token,
    generate_join_code,
    hash_access_token,
    hash_join_code,
)
from ai_kp.platform.sessions.models import AuthenticatedMember


def _public_row(row: sqlite3.Row, *hidden: str) -> dict:
    result = dict(row)
    for field in hidden:
        result.pop(field, None)
    return result


class SecurityRepository:
    connection: sqlite3.Connection

    def player_action_block_reason(
        self, identity: AuthenticatedMember
    ) -> str | None:
        return PlayerActionAuthority(self.connection).block_reason(identity)

    def create_campaign_session(
        self,
        campaign_id: str,
        *,
        title: str | None = None,
        kp_display_name: str = "KP",
    ) -> dict:
        campaign = self.connection.execute(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        if campaign is None:
            raise KeyError(f"Campaign not found: {campaign_id}")
        active = self.connection.execute(
            "SELECT id FROM campaign_sessions WHERE campaign_id = ? AND status = 'active'",
            (campaign_id,),
        ).fetchone()
        if active is not None:
            raise ValueError(f"Campaign already has an active session: {active['id']}")

        session_id = new_id("session")
        member_id = new_id("member")
        join_code = generate_join_code()
        access_token = generate_access_token()
        self.connection.execute(
            """
            INSERT INTO campaign_sessions (id, campaign_id, title, status, join_code_hash)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (
                session_id,
                campaign_id,
                title or f"{campaign['title']} session",
                hash_join_code(join_code),
            ),
        )
        self.connection.execute(
            """
            INSERT INTO session_members
              (id, session_id, campaign_id, role, display_name, token_hash)
            VALUES (?, ?, ?, 'kp', ?, ?)
            """,
            (
                member_id,
                session_id,
                campaign_id,
                kp_display_name,
                hash_access_token(access_token),
            ),
        )
        return {
            "session": self.get_campaign_session(session_id),
            "member": self.get_session_member(member_id),
            "campaign": dict(campaign),
            "access_token": access_token,
            "join_code": join_code,
        }

    def join_campaign_session(
        self,
        join_code: str,
        *,
        display_name: str,
        role: str = "player",
    ) -> dict:
        # The code check and member creation are one linearizable operation.
        # Otherwise a concurrent rotation can commit after this SELECT but
        # before the INSERT, allowing the retired code to create a live token.
        if role not in {"player", "observer"}:
            raise ValueError("Only player or observer may join a session")
        self.begin_immediate()
        session = self.connection.execute(
            """
            SELECT * FROM campaign_sessions
            WHERE join_code_hash = ? AND status = 'active'
            """,
            (hash_join_code(join_code),),
        ).fetchone()
        if session is None:
            raise KeyError("Active session not found for join code")
        member_id = new_id("member")
        access_token = generate_access_token()
        private_history_from_sequence = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM table_messages"
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            INSERT INTO session_members
              (id, session_id, campaign_id, role, display_name, token_hash,
               private_history_from_sequence)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                member_id,
                session["id"],
                session["campaign_id"],
                role,
                display_name,
                hash_access_token(access_token),
                private_history_from_sequence,
            ),
        )
        self.append_realtime_event(
            session_id=str(session["id"]),
            campaign_id=str(session["campaign_id"]),
            audience="kp",
            event_type="session.member_joined",
            resource_type="session_member",
            resource_id=member_id,
            payload={"role": role},
        )
        campaign = self.connection.execute(
            "SELECT * FROM campaigns WHERE id = ?", (session["campaign_id"],)
        ).fetchone()
        return {
            "session": self.get_campaign_session(session["id"]),
            "member": self.get_session_member(member_id),
            "campaign": dict(campaign),
            "access_token": access_token,
        }

    def reissue_campaign_kp_access_token(
        self,
        campaign_id: str,
        *,
        display_name: str,
    ) -> dict:
        rows = self.connection.execute(
            """
            SELECT sm.id, sm.session_id
            FROM session_members sm
            JOIN campaign_sessions cs ON cs.id = sm.session_id
            WHERE sm.campaign_id = ?
              AND sm.role = 'kp'
              AND sm.revoked_at IS NULL
              AND cs.status = 'active'
              AND sm.display_name = ? COLLATE NOCASE
            ORDER BY sm.joined_at, sm.id
            """,
            (campaign_id, display_name),
        ).fetchall()
        if not rows:
            raise KeyError("Active KP not found for this campaign and display name")
        if len(rows) != 1:
            raise ValueError("More than one active KP uses this display name")

        member_id = str(rows[0]["id"])
        session_id = str(rows[0]["session_id"])
        access_token = generate_access_token()
        updated = self.connection.execute(
            """
            UPDATE session_members
            SET token_hash = ?, last_seen_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND revoked_at IS NULL
              AND EXISTS (
                SELECT 1 FROM campaign_sessions cs
                WHERE cs.id = session_members.session_id
                  AND cs.status = 'active'
              )
            """,
            (hash_access_token(access_token), member_id),
        )
        if updated.rowcount != 1:
            raise ValueError("KP credential could not be reissued")

        campaign = self.connection.execute(
            "SELECT * FROM campaigns WHERE id = ?",
            (campaign_id,),
        ).fetchone()
        if campaign is None:
            raise KeyError(f"Campaign not found: {campaign_id}")
        self.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience="kp",
            event_type="session.kp_credential_reissued",
            resource_type="session_member",
            resource_id=member_id,
            payload={"member_id": member_id},
        )
        return {
            "session": self.get_campaign_session(session_id),
            "member": self.get_session_member(member_id),
            "campaign": dict(campaign),
            "access_token": access_token,
        }

    def authenticate_access_token(self, access_token: str) -> AuthenticatedMember | None:
        row = self.connection.execute(
            """
            SELECT sm.*
            FROM session_members sm
            JOIN campaign_sessions cs ON cs.id = sm.session_id
            WHERE sm.token_hash = ?
              AND sm.revoked_at IS NULL
              AND cs.status = 'active'
            """,
            (hash_access_token(access_token),),
        ).fetchone()
        if row is None:
            return None
        return self._authenticated_member(row)

    def is_session_member_active(self, member_id: str, session_id: str) -> bool:
        return self.get_active_session_member(member_id, session_id) is not None

    def get_active_session_member(
        self,
        member_id: str,
        session_id: str,
    ) -> AuthenticatedMember | None:
        row = self.connection.execute(
            """
            SELECT sm.*
            FROM session_members sm
            JOIN campaign_sessions cs ON cs.id = sm.session_id
            WHERE sm.id = ?
              AND sm.session_id = ?
              AND sm.revoked_at IS NULL
              AND cs.status = 'active'
            """,
            (member_id, session_id),
        ).fetchone()
        if row is None:
            return None
        return self._authenticated_member(row)

    def _authenticated_member(self, row: sqlite3.Row) -> AuthenticatedMember:
        seat = self.connection.execute(
            """
            SELECT id FROM session_seats
            WHERE claimed_member_id = ? AND status = 'claimed'
            """,
            (row["id"],),
        ).fetchone()
        return AuthenticatedMember(
            member_id=str(row["id"]),
            session_id=str(row["session_id"]),
            campaign_id=str(row["campaign_id"]),
            role=str(row["role"]),
            display_name=str(row["display_name"]),
            pc_id=str(row["pc_id"]) if row["pc_id"] is not None else None,
            player_profile_id=(
                str(row["player_profile_id"])
                if row["player_profile_id"] is not None
                else None
            ),
            seat_id=str(seat["id"]) if seat is not None else None,
        )

    def get_session_member_connection_status(self, member_id: str, session_id: str) -> str:
        row = self.connection.execute(
            """
            SELECT sm.revoked_at, cs.status AS session_status
            FROM session_members sm
            JOIN campaign_sessions cs ON cs.id = sm.session_id
            WHERE sm.id = ? AND sm.session_id = ?
            """,
            (member_id, session_id),
        ).fetchone()
        if row is None:
            return "missing"
        if row["revoked_at"] is not None:
            return "revoked"
        if row["session_status"] != "active":
            return "closed"
        return "active"

    def get_campaign_session(self, session_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM campaign_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Session not found: {session_id}")
        return _public_row(row, "join_code_hash")

    def get_session_member(self, member_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM session_members WHERE id = ?", (member_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Session member not found: {member_id}")
        return _public_row(row, "token_hash", "private_history_from_sequence")

    def list_session_members(self, session_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM session_members
            WHERE session_id = ? ORDER BY joined_at, id
            """,
            (session_id,),
        ).fetchall()
        return [
            _public_row(row, "token_hash", "private_history_from_sequence")
            for row in rows
        ]

    def close_campaign_session(self, session_id: str) -> dict:
        updated = self.connection.execute(
            """
            UPDATE campaign_sessions
            SET status = 'closed', ended_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'active'
            """,
            (session_id,),
        )
        if updated.rowcount != 1:
            session = self.get_campaign_session(session_id)
            raise ValueError(f"Session is already {session['status']}")
        self.complete_session_timeline_participations(session_id)
        session = self.get_campaign_session(session_id)
        self.append_realtime_event(
            session_id=session_id,
            campaign_id=str(session["campaign_id"]),
            audience="session",
            event_type="session.closed",
            resource_type="campaign_session",
            resource_id=session_id,
            payload={"status": "closed"},
        )
        return session

    def rotate_session_join_code(self, session_id: str) -> dict:
        self.begin_immediate()
        session = self.get_campaign_session(session_id)
        if session["status"] != "active":
            raise ValueError("Only active sessions can rotate their join code")
        join_code = generate_join_code()
        updated = self.connection.execute(
            """
            UPDATE campaign_sessions
            SET join_code_hash = ?
            WHERE id = ? AND status = 'active'
            """,
            (hash_join_code(join_code), session_id),
        )
        if updated.rowcount != 1:
            raise ValueError("Only active sessions can rotate their join code")
        return {"session": self.get_campaign_session(session_id), "join_code": join_code}

    def revoke_session_member(self, session_id: str, member_id: str) -> dict:
        member = self.get_session_member(member_id)
        if member["session_id"] != session_id:
            raise ValueError("Member does not belong to this session")
        if member["role"] == "kp":
            raise ValueError("The KP member cannot be revoked")
        if member["revoked_at"] is not None:
            raise ValueError("The player member is already revoked")
        self.connection.execute(
            "UPDATE session_members SET revoked_at = CURRENT_TIMESTAMP WHERE id = ?",
            (member_id,),
        )
        revoked = self.get_session_member(member_id)
        self.append_realtime_event(
            session_id=session_id,
            campaign_id=str(member["campaign_id"]),
            audience="kp",
            event_type="session.member_revoked",
            resource_type="session_member",
            resource_id=member_id,
        )
        self.append_realtime_event(
            session_id=session_id,
            campaign_id=str(member["campaign_id"]),
            audience="member",
            member_id=member_id,
            event_type="session.credential_revoked",
            resource_type="session_member",
            resource_id=member_id,
        )
        return revoked

    def create_player_action(
        self,
        identity: AuthenticatedMember,
        *,
        action_text: str,
        map_id: str | None = None,
        token_id: str | None = None,
        client_action_id: str | None = None,
    ) -> dict:
        if identity.role != "player":
            raise ValueError("Only players submit player actions")
        if client_action_id:
            existing = self.connection.execute(
                """
                SELECT * FROM player_actions
                WHERE session_id = ? AND member_id = ? AND client_action_id = ?
                """,
                (identity.session_id, identity.member_id, client_action_id),
            ).fetchone()
            if existing is not None:
                return dict(existing)
        resolved_map_id, resolved_location = self._resolve_player_action_position(
            identity,
            map_id=map_id,
            token_id=token_id,
        )
        action_id = new_id("action")
        insert_sql = """
            INSERT INTO player_actions
              (id, session_id, campaign_id, member_id, pc_id, action_text, location,
               map_id, client_action_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            action_id,
            identity.session_id,
            identity.campaign_id,
            identity.member_id,
            identity.pc_id,
            action_text,
            resolved_location,
            resolved_map_id,
            client_action_id,
        )
        if client_action_id:
            inserted = self.connection.execute(f"{insert_sql} ON CONFLICT DO NOTHING", params)
            if inserted.rowcount == 0:
                existing = self.connection.execute(
                    """
                    SELECT * FROM player_actions
                    WHERE session_id = ? AND member_id = ? AND client_action_id = ?
                    """,
                    (identity.session_id, identity.member_id, client_action_id),
                ).fetchone()
                if existing is None:
                    raise ValueError("Player action conflicts with existing state")
                return dict(existing)
        else:
            self.connection.execute(insert_sql, params)
        action = self.get_player_action(action_id)
        self.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=identity.campaign_id,
            audience="kp",
            event_type="player_action.submitted",
            resource_type="player_action",
            resource_id=action_id,
            payload={"status": "submitted"},
        )
        return action

    def _resolve_player_action_position(
        self,
        identity: AuthenticatedMember,
        *,
        map_id: str | None,
        token_id: str | None,
    ) -> tuple[str | None, str | None]:
        if not map_id and not token_id:
            return None, None
        if not identity.pc_id:
            raise ValueError("A PC must be assigned before submitting a map action")

        filters = [
            "m.campaign_id = ?",
            "m.status = 'published'",
            "t.actor_type = 'pc'",
            "t.actor_id = ?",
            "t.visibility IN ('player', 'table')",
            "l.visibility IN ('player', 'table')",
        ]
        params: list[object] = [identity.campaign_id, identity.pc_id]
        if token_id:
            filters.append("t.id = ?")
            params.append(token_id)
        if map_id:
            filters.append("m.id = ?")
            params.append(map_id)
        row = self.connection.execute(
            """
            SELECT t.id AS token_id, m.id AS map_id, l.name AS location_name
            FROM map_tokens t
            JOIN maps m ON m.id = t.map_id
            JOIN map_locations l ON l.id = t.location_id
            WHERE """
            + " AND ".join(filters)
            + " ORDER BY t.created_at, t.id LIMIT 1",
            params,
        )
        row = row.fetchone()
        if row is None:
            raise ValueError("No visible controlled PC token matches this map action")
        return str(row["map_id"]), str(row["location_name"])

    def get_player_action(self, action_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM player_actions WHERE id = ?", (action_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Player action not found: {action_id}")
        return dict(row)

    def require_submitted_player_action(
        self,
        action_id: str,
        campaign_id: str,
        session_id: str,
    ) -> dict:
        action = self.get_player_action(action_id)
        if action["campaign_id"] != campaign_id or action["session_id"] != session_id:
            raise ValueError("Player action does not belong to this campaign session")
        if action["status"] != "submitted" or action["proposal_id"] is not None:
            raise ValueError(f"Player action is already {action['status']}")
        return action

    def link_player_action_to_proposal(
        self,
        action_id: str,
        proposal_id: str,
        campaign_id: str,
    ) -> dict:
        proposal = self.connection.execute(
            "SELECT id FROM turn_proposals WHERE id = ? AND campaign_id = ?",
            (proposal_id, campaign_id),
        ).fetchone()
        if proposal is None:
            raise ValueError("Proposal does not belong to this campaign")
        updated = self.connection.execute(
            """
            UPDATE player_actions
            SET status = 'reviewed', proposal_id = ?
            WHERE id = ?
              AND campaign_id = ?
              AND status = 'submitted'
              AND proposal_id IS NULL
            """,
            (proposal_id, action_id, campaign_id),
        )
        if updated.rowcount != 1:
            action = self.get_player_action(action_id)
            raise ValueError(f"Player action is already {action['status']}")
        action = self.get_player_action(action_id)
        self.append_realtime_event(
            session_id=str(action["session_id"]),
            campaign_id=campaign_id,
            audience="kp",
            event_type="player_action.reviewed",
            resource_type="player_action",
            resource_id=action_id,
            payload={"status": "reviewed"},
        )
        self.append_realtime_event(
            session_id=str(action["session_id"]),
            campaign_id=campaign_id,
            audience="member",
            member_id=str(action["member_id"]),
            event_type="player_action.reviewed",
            resource_type="player_action",
            resource_id=action_id,
            payload={"status": "reviewed"},
        )
        return action

    def resolve_player_action_for_proposal(self, proposal_id: str, status: str) -> None:
        if status not in {"resolved", "rejected"}:
            raise ValueError(f"Unsupported player action resolution: {status}")
        actions = self.connection.execute(
            """
            SELECT id, session_id, campaign_id, member_id
            FROM player_actions
            WHERE proposal_id = ? AND status = 'reviewed'
            """,
            (proposal_id,),
        ).fetchall()
        self.connection.execute(
            """
            UPDATE player_actions
            SET status = ?, resolved_at = CURRENT_TIMESTAMP
            WHERE proposal_id = ? AND status = 'reviewed'
            """,
            (status, proposal_id),
        )
        for action in actions:
            event_type = f"player_action.{status}"
            self.append_realtime_event(
                session_id=str(action["session_id"]),
                campaign_id=str(action["campaign_id"]),
                audience="kp",
                event_type=event_type,
                resource_type="player_action",
                resource_id=str(action["id"]),
                payload={"status": status},
            )
            self.append_realtime_event(
                session_id=str(action["session_id"]),
                campaign_id=str(action["campaign_id"]),
                audience="member",
                member_id=str(action["member_id"]),
                event_type=event_type,
                resource_type="player_action",
                resource_id=str(action["id"]),
                payload={"status": status},
            )

    def list_player_actions(
        self,
        campaign_id: str,
        session_id: str,
        status: str | None = None,
    ) -> list[dict]:
        if status:
            rows = self.connection.execute(
                """
                SELECT pa.*, sm.display_name
                FROM player_actions pa JOIN session_members sm ON sm.id = pa.member_id
                WHERE pa.campaign_id = ? AND pa.session_id = ? AND pa.status = ?
                ORDER BY pa.created_at, pa.id
                """,
                (campaign_id, session_id, status),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT pa.*, sm.display_name
                FROM player_actions pa JOIN session_members sm ON sm.id = pa.member_id
                WHERE pa.campaign_id = ? AND pa.session_id = ?
                ORDER BY pa.created_at, pa.id
                """,
                (campaign_id, session_id),
            ).fetchall()
        return [dict(row) for row in rows]
