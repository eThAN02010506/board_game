"""Migration 81: version persisted text-model configuration for AI call fencing."""

import sqlite3

VERSION = 81
NAME = "model_execution_fence"


def migrate(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(model_configuration)")
    }
    if "version" in columns:
        return
    connection.execute(
        "ALTER TABLE model_configuration ADD COLUMN version INTEGER NOT NULL "
        "DEFAULT 1 CHECK (version > 0)"
    )


__all__ = ["NAME", "VERSION", "migrate"]
