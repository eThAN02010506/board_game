"""Migration 85: append-only lifecycle events for human-KP advice attempts."""

import sqlite3

VERSION = 85
NAME = "director_help_audit_events"


def migrate(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS director_help_audit_events (
          sequence INTEGER PRIMARY KEY AUTOINCREMENT,
          event_id TEXT NOT NULL UNIQUE,
          attempt_id TEXT NOT NULL,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          run_id TEXT NOT NULL REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
          requested_by_member_id TEXT
            REFERENCES session_members(id) ON DELETE SET NULL,
          request_id TEXT NOT NULL CHECK (length(request_id) BETWEEN 8 AND 200),
          event_type TEXT NOT NULL CHECK (event_type IN (
            'requested', 'completed', 'failed', 'cancelled_client',
            'cancelled_control', 'rejected_busy'
          )),
          question TEXT,
          advice_json TEXT,
          response_hash TEXT,
          error_code TEXT,
          duration_ms INTEGER,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (
            (event_type = 'requested'
              AND question IS NOT NULL
              AND length(trim(question)) BETWEEN 1 AND 2000
              AND advice_json IS NULL
              AND response_hash IS NULL
              AND error_code IS NULL
              AND duration_ms IS NULL)
            OR
            (event_type = 'completed'
              AND question IS NULL
              AND advice_json IS NOT NULL
              AND json_valid(advice_json)
              AND json_type(advice_json) = 'object'
              AND response_hash IS NOT NULL
              AND length(response_hash) = 64
              AND response_hash NOT GLOB '*[^0-9a-f]*'
              AND error_code IS NULL
              AND duration_ms IS NOT NULL
              AND duration_ms >= 0)
            OR
            (event_type IN (
                'failed', 'cancelled_client', 'cancelled_control', 'rejected_busy'
              )
              AND question IS NULL
              AND advice_json IS NULL
              AND response_hash IS NULL
              AND error_code IS NOT NULL
              AND length(trim(error_code)) BETWEEN 1 AND 160
              AND error_code GLOB '[a-z]*'
              AND error_code NOT GLOB '*[^a-z0-9_]*'
              AND duration_ms IS NOT NULL
              AND duration_ms >= 0)
          )
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_director_help_audit_requested_attempt
          ON director_help_audit_events(attempt_id)
          WHERE event_type = 'requested'
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_director_help_audit_terminal_attempt
          ON director_help_audit_events(attempt_id)
          WHERE event_type <> 'requested'
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_director_help_audit_campaign_requested_sequence
          ON director_help_audit_events(campaign_id, sequence DESC)
          WHERE event_type = 'requested'
        """,
    )
    for statement in statements:
        connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
