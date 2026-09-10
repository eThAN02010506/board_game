"""Migration 82: bind resumable contract authoring to one model generation."""

import sqlite3

VERSION = 82
NAME = "scenario_contract_model_generation"


def migrate(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(scenario_contract_jobs)")
    }
    if "model_configuration_version" in columns:
        return
    connection.execute(
        "ALTER TABLE scenario_contract_jobs ADD COLUMN "
        "model_configuration_version INTEGER NOT NULL DEFAULT 0 "
        "CHECK (model_configuration_version >= 0)"
    )


__all__ = ["NAME", "VERSION", "migrate"]
