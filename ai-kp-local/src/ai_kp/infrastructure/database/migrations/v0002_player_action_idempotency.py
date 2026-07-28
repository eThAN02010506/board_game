import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 2
NAME = "add_player_action_idempotency"


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(connection, "player_actions", "client_action_id", "TEXT")
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_player_actions_idempotency
        ON player_actions(session_id, member_id, client_action_id)
        WHERE client_action_id IS NOT NULL
        """
    )
