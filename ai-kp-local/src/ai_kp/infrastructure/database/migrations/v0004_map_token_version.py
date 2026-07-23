import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column


VERSION = 4
NAME = "add_map_token_version"


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(connection, "map_tokens", "version", "INTEGER NOT NULL DEFAULT 0")
