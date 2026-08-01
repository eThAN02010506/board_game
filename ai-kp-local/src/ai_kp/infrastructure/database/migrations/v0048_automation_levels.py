"""Migration 48: per-run automation strength and audit events."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 48
NAME = "add_module_run_automation_levels"


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(
        connection,
        "campaign_module_runs",
        "automation_level",
        "TEXT NOT NULL DEFAULT 'conservative' "
        "CHECK (automation_level IN ('conservative', 'balanced', 'ai_kp'))",
    )
    ensure_column(
        connection,
        "campaign_module_runs",
        "automation_reason",
        "TEXT",
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS module_run_automation_events (
          id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
          event_seq INTEGER NOT NULL,
          from_level TEXT NOT NULL
            CHECK (from_level IN ('conservative', 'balanced', 'ai_kp')),
          to_level TEXT NOT NULL
            CHECK (to_level IN ('conservative', 'balanced', 'ai_kp')),
          reason TEXT NOT NULL,
          changed_by_member_id TEXT NOT NULL
            REFERENCES session_members(id) ON DELETE RESTRICT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(run_id, event_seq)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_module_run_automation_events
        ON module_run_automation_events(run_id, event_seq)
        """
    )
