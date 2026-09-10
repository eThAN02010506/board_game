"""Durable lifecycle authority for admitting new player actions."""

from __future__ import annotations

import sqlite3

from ai_kp.platform.sessions.models import AuthenticatedMember


class PlayerActionAuthority:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def block_reason(self, identity: AuthenticatedMember) -> str | None:
        presence = self.connection.execute(
            """
            SELECT state, investigator_id
            FROM campaign_member_presence
            WHERE campaign_id = ? AND member_id = ?
            """,
            (identity.campaign_id, identity.member_id),
        ).fetchone()
        if presence is not None and str(presence["state"]) != "active":
            return {
                "observing": "You are observing; return with an approved character before acting",
                "temporarily_absent": "You are temporarily absent and cannot submit play actions",
                "npc_controlled": "This character is currently controlled by the KP",
            }.get(str(presence["state"]), "This seat is not active in play")

        investigator_id = presence["investigator_id"] if presence is not None else None
        if investigator_id is None and identity.pc_id:
            investigator = self.connection.execute(
                """
                SELECT investigator_id
                FROM campaign_investigators
                WHERE campaign_id = ? AND legacy_pc_id = ?
                """,
                (identity.campaign_id, identity.pc_id),
            ).fetchone()
            investigator_id = (
                investigator["investigator_id"] if investigator is not None else None
            )
        if investigator_id is None:
            return None
        lifecycle = self.connection.execute(
            """
            SELECT state FROM campaign_investigator_lifecycle
            WHERE campaign_id = ? AND investigator_id = ?
            """,
            (identity.campaign_id, investigator_id),
        ).fetchone()
        if lifecycle is None or str(lifecycle["state"]) == "active":
            return None
        return {
            "incapacitated": "The current investigator is incapacitated and cannot act",
            "dead": "The current investigator is dead; observe or confirm a replacement",
            "retired": "The current investigator is retired; confirm a replacement to continue",
            "departed": "The current investigator has left active play",
            "npc_controlled": "The current investigator is currently controlled by the KP",
        }.get(str(lifecycle["state"]), "The current investigator cannot act")


__all__ = ["PlayerActionAuthority"]
