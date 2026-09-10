"""Migration 80: authoritative campaign objectives and progress history."""

import sqlite3

VERSION = 80
NAME = "campaign_objectives"


def migrate(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS campaign_objectives (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          title TEXT NOT NULL,
          public_description TEXT NOT NULL DEFAULT '',
          kp_notes TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'open' CHECK (status IN (
            'open', 'blocked', 'completed', 'failed', 'abandoned'
          )),
          visibility TEXT NOT NULL DEFAULT 'table' CHECK (visibility IN ('table', 'kp')),
          source_refs_json TEXT NOT NULL DEFAULT '[]',
          created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
          version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_campaign_objectives_projection
          ON campaign_objectives(campaign_id, visibility, status, updated_at, id)
        """,
        """
        CREATE TABLE IF NOT EXISTS campaign_objective_events (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          objective_id TEXT NOT NULL REFERENCES campaign_objectives(id) ON DELETE CASCADE,
          command_id TEXT NOT NULL,
          from_status TEXT,
          to_status TEXT NOT NULL,
          public_progress TEXT NOT NULL DEFAULT '',
          kp_notes TEXT NOT NULL DEFAULT '',
          actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
          source_refs_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(campaign_id, command_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_campaign_objective_events
          ON campaign_objective_events(campaign_id, objective_id, created_at, id)
        """,
    )
    for statement in statements:
        connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
