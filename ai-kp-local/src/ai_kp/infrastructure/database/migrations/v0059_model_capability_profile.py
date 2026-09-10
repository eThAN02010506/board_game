"""Migration 59: explicit semantic capability profile for the text model."""

import sqlite3

VERSION = 59
NAME = "model_capability_profile"


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE model_configuration ADD COLUMN semantic_profile TEXT NOT NULL "
        "DEFAULT 'small' CHECK (semantic_profile IN ('small', 'large'))"
    )
