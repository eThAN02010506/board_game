"""Authoritative immutable table communications with server-side visibility."""

from __future__ import annotations

from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.sessions.models import AuthenticatedMember


class TableMessageRepository(SQLiteRepository):
    def list_safe_table_members(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, display_name, role, joined_at
            FROM session_members
            WHERE session_id = ? AND revoked_at IS NULL
            ORDER BY joined_at, id
            """,
            (session_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def create_table_message(
        self,
        *,
        identity: AuthenticatedMember,
        audience: str,
        content: str,
        recipient_member_id: str | None,
        client_message_id: str,
    ) -> dict[str, Any]:
        self.begin_immediate()
        existing = self.connection.execute(
            """
            SELECT id, audience, content, recipient_member_id
            FROM table_messages
            WHERE sender_member_id = ? AND client_message_id = ?
            """,
            (identity.member_id, client_message_id),
        ).fetchone()
        if existing is not None:
            if (
                existing["audience"] != audience
                or existing["content"] != content
                or existing["recipient_member_id"] != recipient_member_id
            ):
                raise ValueError("client_message_id was already used for different content")
            return self.get_table_message_for(identity, str(existing["id"]))
        message_id = new_id("msg")
        self.connection.execute(
            """
            INSERT INTO table_messages
              (id, campaign_id, session_id, sender_member_id, audience,
               recipient_member_id, content, client_message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                identity.campaign_id,
                identity.session_id,
                identity.member_id,
                audience,
                recipient_member_id,
                content,
                client_message_id,
            ),
        )
        self._notify_table_message(identity, message_id, audience, recipient_member_id)
        return self.get_table_message_for(identity, message_id)

    def get_table_message_for(
        self, identity: AuthenticatedMember, message_id: str
    ) -> dict[str, Any]:
        rows = self._visible_message_rows(identity, message_id=message_id, limit=1)
        if not rows:
            raise KeyError("Table message not found")
        return self._project_message(rows[0])

    def list_visible_table_messages(
        self,
        identity: AuthenticatedMember,
        *,
        before_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self._visible_message_rows(
            identity,
            before_id=before_id,
            limit=max(1, min(limit, 200)),
        )
        return [self._project_message(row) for row in reversed(rows)]

    def _visible_message_rows(
        self,
        identity: AuthenticatedMember,
        *,
        message_id: str | None = None,
        before_id: str | None = None,
        limit: int,
    ) -> list[Any]:
        before = None
        if before_id:
            before = self.connection.execute(
                "SELECT sequence FROM table_messages WHERE id = ?",
                (before_id,),
            ).fetchone()
            if before is None:
                raise KeyError("Table message cursor not found")
        return self.connection.execute(
            """
            SELECT message.*,
                   sender.display_name AS sender_display_name,
                   sender.role AS sender_role,
                   recipient.display_name AS recipient_display_name,
                   recipient.role AS recipient_role
            FROM table_messages message
            JOIN session_members sender ON sender.id = message.sender_member_id
            JOIN session_members viewer ON viewer.id = ?
            LEFT JOIN session_members recipient
              ON recipient.id = message.recipient_member_id
            WHERE message.session_id = ? AND message.campaign_id = ?
              AND (? IS NULL OR message.id = ?)
              AND (
                message.audience IN ('table', 'announcement')
                OR (message.audience = 'party' AND ? IN ('kp', 'player')
                    AND message.sequence >= viewer.private_history_from_sequence)
                OR (message.audience = 'direct' AND (
                  message.sender_member_id = ? OR message.recipient_member_id = ?
                ))
              )
              AND (
                ? IS NULL OR message.sequence < ?
              )
            ORDER BY message.sequence DESC
            LIMIT ?
            """,
            (
                identity.member_id,
                identity.session_id,
                identity.campaign_id,
                message_id,
                message_id,
                identity.role,
                identity.member_id,
                identity.member_id,
                before["sequence"] if before is not None else None,
                before["sequence"] if before is not None else None,
                limit,
            ),
        ).fetchall()

    def _notify_table_message(
        self,
        identity: AuthenticatedMember,
        message_id: str,
        audience: str,
        recipient_member_id: str | None,
    ) -> None:
        payload = {"message_id": message_id, "audience": audience}
        if audience in {"table", "announcement"}:
            self.append_realtime_event(
                session_id=identity.session_id,
                campaign_id=identity.campaign_id,
                audience="session",
                event_type="table_message.created",
                resource_type="table_message",
                resource_id=message_id,
                payload=payload,
            )
            return
        if audience == "party":
            recipients = self.connection.execute(
                """
                SELECT id FROM session_members
                WHERE session_id = ? AND revoked_at IS NULL
                  AND role IN ('kp', 'player')
                """,
                (identity.session_id,),
            ).fetchall()
            member_ids = {str(row["id"]) for row in recipients}
        else:
            member_ids = {identity.member_id, str(recipient_member_id)}
        for member_id in sorted(member_ids):
            self.append_realtime_event(
                session_id=identity.session_id,
                campaign_id=identity.campaign_id,
                audience="member",
                member_id=member_id,
                event_type="table_message.created",
                resource_type="table_message",
                resource_id=message_id,
                payload=payload,
            )

    @staticmethod
    def _project_message(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "campaign_id": row["campaign_id"],
            "audience": row["audience"],
            "content": row["content"],
            "sender": {
                "member_id": row["sender_member_id"],
                "display_name": row["sender_display_name"],
                "role": row["sender_role"],
            },
            "recipient": (
                {
                    "member_id": row["recipient_member_id"],
                    "display_name": row["recipient_display_name"],
                    "role": row["recipient_role"],
                }
                if row["recipient_member_id"] is not None
                else None
            ),
            "created_at": row["created_at"],
        }


__all__ = ["TableMessageRepository"]
