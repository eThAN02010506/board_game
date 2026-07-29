"""Migration 20: explicit, spoiler-scoped campaign module runs."""

import sqlite3

VERSION = 20
NAME = "add_campaign_module_runs"


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_module_runs (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE RESTRICT,
          module_source_hash TEXT,
          status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'paused', 'completed')),
          current_scene_key TEXT,
          active_spoiler_tags_json TEXT NOT NULL DEFAULT '[]',
          state_json TEXT NOT NULL DEFAULT '{}',
          started_by_member_id TEXT
            REFERENCES session_members(id) ON DELETE SET NULL,
          started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_campaign_module_runs_one_active
          ON campaign_module_runs(campaign_id)
          WHERE status = 'active'
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_campaign_module_runs_campaign_started
          ON campaign_module_runs(campaign_id, started_at DESC)
        """
    )
