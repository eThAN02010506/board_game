"""Add immutable, optimistic-concurrency memory curation actions."""

import sqlite3

VERSION = 31
NAME = "add_memory_curation_actions"

DDL = """
CREATE TABLE IF NOT EXISTS memory_curation_actions (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  classification TEXT NOT NULL
    CHECK (classification IN ('major', 'side', 'npc', 'clue', 'other')),
  importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
  hidden INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
  reason TEXT NOT NULL CHECK (length(trim(reason)) BETWEEN 1 AND 1000),
  supersedes_action_id TEXT
    REFERENCES memory_curation_actions(id) ON DELETE RESTRICT,
  created_by_member_id TEXT NOT NULL
    REFERENCES session_members(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(supersedes_action_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_curation_memory_created
  ON memory_curation_actions(memory_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_memory_curation_campaign_created
  ON memory_curation_actions(campaign_id, created_at DESC, id DESC);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
