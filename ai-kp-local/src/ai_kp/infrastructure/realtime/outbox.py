"""SQLite transactional realtime outbox and replay adapter."""

import json
import sqlite3
import time
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.security.tokens import generate_realtime_ticket, hash_realtime_ticket
from ai_kp.platform.sessions.models import AuthenticatedMember


class RealtimeRepository:
    connection: sqlite3.Connection

    def issue_realtime_ticket(
        self,
        identity: AuthenticatedMember,
        *,
        ttl_seconds: int = 30,
    ) -> dict[str, Any]:
        ttl = max(5, min(ttl_seconds, 120))
        now = int(time.time())
        ticket = generate_realtime_ticket()
        ticket_id = new_id("wst")
        self.connection.execute(
            "DELETE FROM realtime_tickets WHERE expires_at < ?",
            (now - 3600,),
        )
        self.connection.execute(
            """
            INSERT INTO realtime_tickets
              (id, ticket_hash, session_id, campaign_id, member_id, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                hash_realtime_ticket(ticket),
                identity.session_id,
                identity.campaign_id,
                identity.member_id,
                now + ttl,
            ),
        )
        return {
            "ticket": ticket,
            "expires_in": ttl,
            "expires_at": now + ttl,
        }

    def consume_realtime_ticket(self, ticket: str) -> AuthenticatedMember | None:
        started_transaction = not self.connection.in_transaction
        if started_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT
                  rt.id AS ticket_id,
                  sm.id AS member_id,
                  sm.session_id,
                  sm.campaign_id,
                  sm.role,
                  sm.display_name,
                  sm.pc_id
                FROM realtime_tickets rt
                JOIN session_members sm ON sm.id = rt.member_id
                JOIN campaign_sessions cs ON cs.id = rt.session_id
                WHERE rt.ticket_hash = ?
                  AND rt.consumed_at IS NULL
                  AND rt.expires_at >= ?
                  AND sm.revoked_at IS NULL
                  AND cs.status = 'active'
                  AND sm.session_id = rt.session_id
                  AND sm.campaign_id = rt.campaign_id
                """,
                (hash_realtime_ticket(ticket), int(time.time())),
            ).fetchone()
            if row is None:
                if started_transaction:
                    self.connection.rollback()
                return None
            consumed = self.connection.execute(
                """
                UPDATE realtime_tickets
                SET consumed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND consumed_at IS NULL
                """,
                (row["ticket_id"],),
            )
            if consumed.rowcount != 1:
                if started_transaction:
                    self.connection.rollback()
                return None
            identity = AuthenticatedMember(
                member_id=str(row["member_id"]),
                session_id=str(row["session_id"]),
                campaign_id=str(row["campaign_id"]),
                role=str(row["role"]),
                display_name=str(row["display_name"]),
                pc_id=str(row["pc_id"]) if row["pc_id"] is not None else None,
            )
            if started_transaction:
                self.connection.commit()
            return identity
        except Exception:
            if started_transaction:
                self.connection.rollback()
            raise

    def append_realtime_event(
        self,
        *,
        session_id: str,
        campaign_id: str,
        event_type: str,
        audience: str = "session",
        member_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if audience not in {"session", "kp", "member"}:
            raise ValueError(f"Unsupported realtime audience: {audience}")
        if (audience == "member") != (member_id is not None):
            raise ValueError("Member audience requires exactly one target member")
        session = self.connection.execute(
            "SELECT campaign_id FROM campaign_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if session is None or session["campaign_id"] != campaign_id:
            raise ValueError("Realtime session does not belong to this campaign")
        if member_id is not None:
            member = self.connection.execute(
                """
                SELECT id FROM session_members
                WHERE id = ? AND session_id = ? AND campaign_id = ?
                """,
                (member_id, session_id, campaign_id),
            ).fetchone()
            if member is None:
                raise ValueError("Realtime target member does not belong to this session")
        event_key = new_id("rte")
        cursor = self.connection.execute(
            """
            INSERT INTO realtime_events
              (event_key, session_id, campaign_id, audience, member_id, event_type,
               resource_type, resource_id, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_key,
                session_id,
                campaign_id,
                audience,
                member_id,
                event_type,
                resource_type,
                resource_id,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        ).lastrowid
        return self.get_realtime_event(int(cursor))

    def get_realtime_event(self, cursor: int) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM realtime_events WHERE id = ?",
            (cursor,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Realtime event not found: {cursor}")
        return self._decode_realtime_event(row)

    def list_visible_realtime_events(
        self,
        *,
        session_id: str,
        role: str,
        member_id: str,
        after_cursor: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if role not in {"kp", "player", "observer"}:
            raise ValueError(f"Unsupported realtime role: {role}")
        bounded_limit = max(1, min(limit, 200))
        rows = self.connection.execute(
            """
            SELECT event.* FROM realtime_events event
            JOIN session_members viewer
              ON viewer.id = ? AND viewer.session_id = event.session_id
            JOIN campaign_sessions session ON session.id = event.session_id
            WHERE event.session_id = ?
              AND event.id > ?
              AND viewer.revoked_at IS NULL
              AND viewer.role = ?
              AND viewer.campaign_id = event.campaign_id
              AND session.status = 'active'
              AND (
                (event.audience = 'session' AND (
                  ? != 'observer' OR event.event_type IN (
                    'table_message.created', 'session.safety_paused',
                    'session.safety_resolved', 'session.closed', 'world.updated',
                    'map.published'
                  )
                ))
                OR (event.audience = 'kp' AND ? = 'kp')
                OR (event.audience = 'member' AND event.member_id = ?)
              )
            ORDER BY event.id ASC
            LIMIT ?
            """,
            (
                member_id,
                session_id,
                max(0, after_cursor),
                role,
                role,
                role,
                member_id,
                bounded_limit,
            ),
        ).fetchall()
        return [self._decode_realtime_event(row) for row in rows]

    def resolve_visible_realtime_cursor(
        self,
        *,
        event_key: str,
        session_id: str,
        role: str,
        member_id: str,
    ) -> int | None:
        if role not in {"kp", "player", "observer"}:
            raise ValueError(f"Unsupported realtime role: {role}")
        row = self.connection.execute(
            """
            SELECT event.id FROM realtime_events event
            JOIN session_members viewer
              ON viewer.id = ? AND viewer.session_id = event.session_id
            JOIN campaign_sessions session ON session.id = event.session_id
            WHERE event.event_key = ?
              AND event.session_id = ?
              AND viewer.revoked_at IS NULL
              AND viewer.role = ?
              AND viewer.campaign_id = event.campaign_id
              AND session.status = 'active'
              AND (
                (event.audience = 'session' AND (
                  ? != 'observer' OR event.event_type IN (
                    'table_message.created', 'session.safety_paused',
                    'session.safety_resolved', 'session.closed', 'world.updated',
                    'map.published'
                  )
                ))
                OR (event.audience = 'kp' AND ? = 'kp')
                OR (event.audience = 'member' AND event.member_id = ?)
              )
            """,
            (member_id, event_key, session_id, role, role, role, member_id),
        ).fetchone()
        return int(row["id"]) if row is not None else None

    @staticmethod
    def realtime_event_for_wire(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "cursor": event["event_key"],
            "session_id": event["session_id"],
            "campaign_id": event["campaign_id"],
            "event_type": event["event_type"],
            "resource_type": event["resource_type"],
            "resource_id": event["resource_id"],
            "payload": event["payload"],
            "created_at": event["created_at"],
        }

    @staticmethod
    def _decode_realtime_event(row: sqlite3.Row) -> dict[str, Any]:
        event = dict(row)
        try:
            event["payload"] = json.loads(event.pop("payload_json"))
        except json.JSONDecodeError:
            event["payload"] = {}
            event.pop("payload_json", None)
        return event
