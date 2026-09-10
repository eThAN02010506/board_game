"""Prevent newly joined members from inheriting old private table history."""

import sqlite3

VERSION = 74
NAME = "private_message_history_boundary"


def migrate(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(session_members)")
    }
    if "private_history_from_sequence" in columns:
        return
    connection.execute(
        """
        ALTER TABLE session_members
        ADD COLUMN private_history_from_sequence INTEGER NOT NULL DEFAULT 1
        CHECK (private_history_from_sequence >= 1)
        """
    )
