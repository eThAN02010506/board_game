"""Add typed campaign-world entity state candidates to turn proposals."""

import sqlite3

VERSION = 90
NAME = "add_proposed_world_entity_states"


def migrate(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(turn_proposals)")
    }
    if "proposed_world_entity_states_json" not in columns:
        connection.execute(
            "ALTER TABLE turn_proposals "
            "ADD COLUMN proposed_world_entity_states_json TEXT NOT NULL DEFAULT '[]' "
            "CHECK (json_valid(proposed_world_entity_states_json))"
        )


__all__ = ["NAME", "VERSION", "migrate"]
