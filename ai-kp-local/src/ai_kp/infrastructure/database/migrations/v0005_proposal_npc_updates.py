import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column


VERSION = 5
NAME = "add_proposed_npc_updates_to_turn_proposals"


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(
        connection,
        "turn_proposals",
        "proposed_npc_updates_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
