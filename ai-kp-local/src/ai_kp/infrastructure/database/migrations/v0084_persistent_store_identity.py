"""Migration 84: give each physical SQLite store one durable identity."""

import sqlite3
import uuid

VERSION = 84
NAME = "persistent_store_identity"


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS persistent_store_identity (
          singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
          persistent_store_id TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO persistent_store_identity
          (singleton, persistent_store_id)
        VALUES (1, ?)
        """,
        (f"store_{uuid.uuid4().hex}",),
    )


__all__ = ["NAME", "VERSION", "migrate"]
