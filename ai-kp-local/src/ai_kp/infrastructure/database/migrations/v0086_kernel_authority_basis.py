"""Migration 86: persist the authority basis for action command receipts."""

import sqlite3

VERSION = 86
NAME = "kernel_authority_basis"


def migrate(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(scenario_command_batches)"
        ).fetchall()
    }
    if "authority_basis_json" in columns:
        return
    connection.execute(
        """
        ALTER TABLE scenario_command_batches
        ADD COLUMN authority_basis_json TEXT
          CHECK (
            authority_basis_json IS NULL
            OR (json_valid(authority_basis_json) AND json_type(authority_basis_json) = 'object')
          )
        """
    )


__all__ = ["NAME", "VERSION", "migrate"]
