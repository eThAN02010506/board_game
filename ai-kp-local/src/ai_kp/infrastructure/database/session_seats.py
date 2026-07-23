"""SQLite adapter for stable session seats and one-time invitations."""

import sqlite3
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.security.tokens import (
    generate_access_token,
    generate_seat_invitation_code,
    hash_access_token,
    hash_seat_invitation_code,
)
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class SessionSeatRepository(SQLiteRepository):
    """Persist per-seat invitations and stable player-profile claims."""

    connection: sqlite3.Connection

    def get_session_seat(self, seat_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT ss.*, pp.display_name AS profile_display_name,
                   sm.display_name AS member_display_name, pc.name AS pc_name,
                   EXISTS(
                     SELECT 1 FROM seat_invitations si
                     WHERE si.seat_id = ss.id AND si.status = 'active'
                   ) AS has_active_invitation
            FROM session_seats ss
            LEFT JOIN player_profiles pp ON pp.id = ss.player_profile_id
            LEFT JOIN session_members sm ON sm.id = ss.claimed_member_id
            LEFT JOIN player_characters pc ON pc.id = ss.assigned_pc_id
            WHERE ss.id = ?
            """,
            (seat_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Session seat not found: {seat_id}")
        return dict(row)

    def list_session_seats(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT id FROM session_seats WHERE session_id = ? ORDER BY created_at, id",
            (session_id,),
        ).fetchall()
        return [self.get_session_seat(str(row["id"])) for row in rows]

    def list_player_session_seats(self, profile_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT ss.id, cs.title AS session_title, cs.status AS session_status,
                   c.title AS campaign_title
            FROM session_seats ss
            JOIN campaign_sessions cs ON cs.id = ss.session_id
            JOIN campaigns c ON c.id = ss.campaign_id
            WHERE ss.player_profile_id = ?
            ORDER BY cs.started_at DESC, ss.created_at DESC
            """,
            (profile_id,),
        ).fetchall()
        result = []
        for row in rows:
            seat = self.get_session_seat(str(row["id"]))
            seat.update(
                {
                    "session_title": row["session_title"],
                    "session_status": row["session_status"],
                    "campaign_title": row["campaign_title"],
                }
            )
            result.append(seat)
        return result

    def create_session_seat(
        self,
        session_id: str,
        *,
        label: str,
        created_by_member_id: str,
        pc_id: str | None = None,
    ) -> dict[str, Any]:
        session = self.get_campaign_session(session_id)
        if session["status"] != "active":
            raise ValueError("Seats can only be created for an active session")
        creator = self.get_session_member(created_by_member_id)
        if creator["session_id"] != session_id or creator["role"] != "kp":
            raise ValueError("Only this session's KP can create seats")
        normalized_label = label.strip()
        if not normalized_label:
            raise ValueError("Seat label is required")
        duplicate = self.connection.execute(
            """
            SELECT id FROM session_seats
            WHERE session_id = ? AND label = ? COLLATE NOCASE AND status != 'revoked'
            """,
            (session_id, normalized_label),
        ).fetchone()
        if duplicate is not None:
            raise ValueError("An active seat already uses this label")
        if pc_id:
            self._validate_seat_pc(session_id, str(session["campaign_id"]), pc_id)

        seat_id = new_id("seat")
        self.connection.execute(
            """
            INSERT INTO session_seats
              (id, session_id, campaign_id, label, assigned_pc_id, created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                seat_id,
                session_id,
                session["campaign_id"],
                normalized_label,
                pc_id,
                created_by_member_id,
            ),
        )
        invitation_code = self._issue_seat_invitation(seat_id)
        self.append_realtime_event(
            session_id=session_id,
            campaign_id=str(session["campaign_id"]),
            audience="kp",
            event_type="session.seat_created",
            resource_type="session_seat",
            resource_id=seat_id,
            payload={"label": normalized_label, "pc_id": pc_id},
        )
        return {"seat": self.get_session_seat(seat_id), "invitation_code": invitation_code}

    def reissue_seat_invitation(self, session_id: str, seat_id: str) -> dict[str, Any]:
        seat = self.get_session_seat(seat_id)
        if seat["session_id"] != session_id:
            raise ValueError("Seat does not belong to this session")
        if seat["status"] != "open":
            raise ValueError("Only an open seat can receive a new invitation")
        invitation_code = self._issue_seat_invitation(seat_id)
        return {"seat": self.get_session_seat(seat_id), "invitation_code": invitation_code}

    def claim_session_seat(
        self,
        invitation_code: str,
        *,
        player_profile_id: str,
        display_name: str,
    ) -> dict[str, Any]:
        invitation = self.connection.execute(
            """
            SELECT si.id AS invitation_id, ss.*
            FROM seat_invitations si
            JOIN session_seats ss ON ss.id = si.seat_id
            JOIN campaign_sessions cs ON cs.id = ss.session_id
            WHERE si.invitation_hash = ? AND si.status = 'active'
              AND ss.status = 'open' AND cs.status = 'active'
            """,
            (hash_seat_invitation_code(invitation_code),),
        ).fetchone()
        if invitation is None:
            raise KeyError("Active seat invitation not found")
        session_id = str(invitation["session_id"])
        campaign_id = str(invitation["campaign_id"])
        duplicate_profile = self.connection.execute(
            """
            SELECT id FROM session_seats
            WHERE session_id = ? AND player_profile_id = ? AND status = 'claimed'
            """,
            (session_id, player_profile_id),
        ).fetchone()
        if duplicate_profile is not None:
            raise ValueError("This player profile already owns a seat in the session")
        pc_id = str(invitation["assigned_pc_id"]) if invitation["assigned_pc_id"] else None
        if pc_id:
            self._validate_seat_pc(session_id, campaign_id, pc_id, seat_id=str(invitation["id"]))

        consumed = self.connection.execute(
            """
            UPDATE seat_invitations
            SET status = 'consumed', consumed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'active'
            """,
            (invitation["invitation_id"],),
        )
        if consumed.rowcount != 1:
            raise ValueError("Seat invitation was already consumed")

        member_id = new_id("member")
        access_token = generate_access_token()
        self.connection.execute(
            """
            INSERT INTO session_members
              (id, session_id, campaign_id, role, display_name, pc_id, token_hash,
               player_profile_id)
            VALUES (?, ?, ?, 'player', ?, ?, ?, ?)
            """,
            (
                member_id,
                session_id,
                campaign_id,
                display_name,
                pc_id,
                hash_access_token(access_token),
                player_profile_id,
            ),
        )
        claimed = self.connection.execute(
            """
            UPDATE session_seats
            SET status = 'claimed', player_profile_id = ?, claimed_member_id = ?,
                claimed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'open'
            """,
            (player_profile_id, member_id, invitation["id"]),
        )
        if claimed.rowcount != 1:
            raise ValueError("Seat was already claimed")
        self.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience="kp",
            event_type="session.seat_claimed",
            resource_type="session_seat",
            resource_id=str(invitation["id"]),
            payload={"member_id": member_id},
        )
        return self._seat_session_bundle(str(invitation["id"]), member_id, access_token)

    def recover_session_seat(self, seat_id: str, profile_id: str) -> dict[str, Any]:
        seat = self.get_session_seat(seat_id)
        if seat["player_profile_id"] != profile_id:
            raise KeyError("Recoverable session seat not found")
        if seat["status"] != "claimed" or not seat["claimed_member_id"]:
            raise ValueError("Only a claimed seat can be recovered")
        member = self.get_session_member(str(seat["claimed_member_id"]))
        session = self.get_campaign_session(str(seat["session_id"]))
        if member["revoked_at"] is not None or session["status"] != "active":
            raise ValueError("This session seat is no longer active")
        access_token = generate_access_token()
        self.connection.execute(
            "UPDATE session_members SET token_hash = ?, last_seen_at = CURRENT_TIMESTAMP WHERE id = ?",
            (hash_access_token(access_token), member["id"]),
        )
        return self._seat_session_bundle(seat_id, str(member["id"]), access_token)

    def revoke_session_seat(self, session_id: str, seat_id: str) -> dict[str, Any]:
        seat = self.get_session_seat(seat_id)
        if seat["session_id"] != session_id:
            raise ValueError("Seat does not belong to this session")
        if seat["status"] == "revoked":
            raise ValueError("Seat is already revoked")
        self.connection.execute(
            """
            UPDATE session_seats
            SET status = 'revoked', revoked_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (seat_id,),
        )
        self.connection.execute(
            """
            UPDATE seat_invitations
            SET status = 'revoked', revoked_at = CURRENT_TIMESTAMP
            WHERE seat_id = ? AND status = 'active'
            """,
            (seat_id,),
        )
        if seat["claimed_member_id"]:
            self.connection.execute(
                """
                UPDATE session_members SET revoked_at = CURRENT_TIMESTAMP
                WHERE id = ? AND revoked_at IS NULL
                """,
                (seat["claimed_member_id"],),
            )
        self.append_realtime_event(
            session_id=session_id,
            campaign_id=str(seat["campaign_id"]),
            audience="session",
            event_type="session.seat_revoked",
            resource_type="session_seat",
            resource_id=seat_id,
        )
        return self.get_session_seat(seat_id)

    def assign_session_seat_pc(
        self,
        session_id: str,
        seat_id: str,
        pc_id: str | None,
    ) -> dict[str, Any]:
        seat = self.get_session_seat(seat_id)
        if seat["session_id"] != session_id or seat["status"] == "revoked":
            raise ValueError("Target must be an active seat in this session")
        if pc_id:
            self._validate_seat_pc(session_id, str(seat["campaign_id"]), pc_id, seat_id=seat_id)
        if seat["claimed_member_id"]:
            if pc_id:
                self.assign_member_pc(session_id, str(seat["claimed_member_id"]), pc_id)
            else:
                self.connection.execute(
                    "UPDATE session_members SET pc_id = NULL WHERE id = ?",
                    (seat["claimed_member_id"],),
                )
        self.connection.execute(
            """
            UPDATE session_seats SET assigned_pc_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (pc_id, seat_id),
        )
        return self.get_session_seat(seat_id)

    def seat_for_member(self, member_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT id FROM session_seats WHERE claimed_member_id = ?",
            (member_id,),
        ).fetchone()
        return self.get_session_seat(str(row["id"])) if row is not None else None

    def sync_member_seat_pc(self, member_id: str, pc_id: str | None) -> None:
        self.connection.execute(
            """
            UPDATE session_seats SET assigned_pc_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE claimed_member_id = ? AND status = 'claimed'
            """,
            (pc_id, member_id),
        )

    def _issue_seat_invitation(self, seat_id: str) -> str:
        self.connection.execute(
            """
            UPDATE seat_invitations
            SET status = 'revoked', revoked_at = CURRENT_TIMESTAMP
            WHERE seat_id = ? AND status = 'active'
            """,
            (seat_id,),
        )
        invitation_code = generate_seat_invitation_code()
        self.connection.execute(
            """
            INSERT INTO seat_invitations (id, seat_id, invitation_hash)
            VALUES (?, ?, ?)
            """,
            (
                new_id("invite"),
                seat_id,
                hash_seat_invitation_code(invitation_code),
            ),
        )
        return invitation_code

    def _validate_seat_pc(
        self,
        session_id: str,
        campaign_id: str,
        pc_id: str,
        *,
        seat_id: str | None = None,
    ) -> None:
        pc = self.connection.execute(
            "SELECT id FROM player_characters WHERE id = ? AND campaign_id = ?",
            (pc_id, campaign_id),
        ).fetchone()
        if pc is None:
            raise ValueError("PC does not belong to this campaign")
        member = self.connection.execute(
            """
            SELECT id FROM session_members
            WHERE session_id = ? AND pc_id = ? AND revoked_at IS NULL
            """,
            (session_id, pc_id),
        ).fetchone()
        if member is not None:
            current_seat = self.seat_for_member(str(member["id"]))
            if current_seat is None or current_seat["id"] != seat_id:
                raise ValueError("PC is already controlled by another active member")
        seat = self.connection.execute(
            """
            SELECT id FROM session_seats
            WHERE session_id = ? AND assigned_pc_id = ? AND status != 'revoked'
              AND (? IS NULL OR id != ?)
            """,
            (session_id, pc_id, seat_id, seat_id),
        ).fetchone()
        if seat is not None:
            raise ValueError("PC is already reserved for another seat")

    def _seat_session_bundle(
        self,
        seat_id: str,
        member_id: str,
        access_token: str,
    ) -> dict[str, Any]:
        seat = self.get_session_seat(seat_id)
        session = self.get_campaign_session(str(seat["session_id"]))
        member = self.get_session_member(member_id)
        campaign = self.connection.execute(
            "SELECT * FROM campaigns WHERE id = ?",
            (seat["campaign_id"],),
        ).fetchone()
        return {
            "session": session,
            "member": member,
            "campaign": dict(campaign),
            "seat": seat,
            "access_token": access_token,
        }


__all__ = ["SessionSeatRepository"]
