"""Add typed world-fact candidates to turn proposals."""

import sqlite3

VERSION = 34
NAME = "add_proposed_world_facts"


def migrate(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(turn_proposals)")
    }
    if "proposed_facts_json" not in columns:
        connection.execute(
            "ALTER TABLE turn_proposals "
            "ADD COLUMN proposed_facts_json TEXT NOT NULL DEFAULT '[]' "
            "CHECK (json_valid(proposed_facts_json))"
        )


__all__ = ["NAME", "VERSION", "migrate"]
