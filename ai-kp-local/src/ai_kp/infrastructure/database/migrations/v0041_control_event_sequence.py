"""Migration 41: stable per-run ordering for director control audit events."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 41
NAME = "add_director_control_event_sequence"


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(connection, "module_run_control_events", "event_seq", "INTEGER")
    connection.execute(
        """
        UPDATE module_run_control_events AS event
        SET event_seq = (
          SELECT COUNT(*)
          FROM module_run_control_events AS prior
          WHERE prior.run_id = event.run_id
            AND prior.rowid <= event.rowid
        )
        WHERE event_seq IS NULL
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_module_run_control_event_sequence
        ON module_run_control_events(run_id, event_seq)
        """
    )
