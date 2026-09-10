"""Migration 76: restart-safe player encounter action consent."""

import sqlite3

VERSION = 76
NAME = "encounter_action_requests"


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS encounter_action_requests (
          id TEXT PRIMARY KEY,
          encounter_id TEXT NOT NULL REFERENCES coc7_encounters(id) ON DELETE CASCADE,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
          member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
          participant_id TEXT NOT NULL,
          client_action_id TEXT NOT NULL,
          encounter_version INTEGER NOT NULL CHECK (encounter_version >= 0),
          action_text TEXT NOT NULL,
          action_key TEXT NOT NULL,
          target_id TEXT,
          status TEXT NOT NULL CHECK (status IN (
            'awaiting_confirmation', 'needs_attention', 'confirmed',
            'committed', 'cancelled', 'failed'
          )),
          preview_json TEXT NOT NULL DEFAULT '{}',
          prepared_command_json TEXT,
          result_json TEXT,
          version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          committed_at TEXT,
          UNIQUE(session_id, client_action_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_encounter_action_requests_active
          ON encounter_action_requests(encounter_id, member_id, status, updated_at)
        """
    )


__all__ = ["NAME", "VERSION", "migrate"]
