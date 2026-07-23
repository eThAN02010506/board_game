import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column


VERSION = 3
NAME = "add_map_status"


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(connection, "maps", "status", "TEXT NOT NULL DEFAULT 'draft'")
