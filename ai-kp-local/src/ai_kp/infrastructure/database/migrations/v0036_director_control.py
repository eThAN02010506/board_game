"""Migration 36: durable AI pause and human-KP control handoff."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 36
NAME = "add_director_control_handoff"


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(
        connection,
        "campaign_module_runs",
        "director_control_mode",
        "TEXT NOT NULL DEFAULT 'ai_assist' "
        "CHECK (director_control_mode IN ('ai_assist', 'safety_paused', 'human_kp'))",
    )
    ensure_column(connection, "campaign_module_runs", "director_control_reason", "TEXT")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS module_run_control_events (
          id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
          from_mode TEXT NOT NULL
            CHECK (from_mode IN ('ai_assist', 'safety_paused', 'human_kp')),
          to_mode TEXT NOT NULL
            CHECK (to_mode IN ('ai_assist', 'safety_paused', 'human_kp')),
          reason TEXT NOT NULL,
          changed_by_member_id TEXT NOT NULL
            REFERENCES session_members(id) ON DELETE RESTRICT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_module_run_control_events "
        "ON module_run_control_events(run_id, created_at)"
    )
