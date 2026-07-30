"""Migration 37: player handouts, reveals, pins, links and read receipts."""

import sqlite3

VERSION = 37
NAME = "add_player_handouts"

DDL = """
CREATE TABLE IF NOT EXISTS campaign_handouts (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'clue'
    CHECK (kind IN ('clue', 'handbook', 'image', 'note')),
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'revealed', 'withdrawn')),
  pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
  link_type TEXT CHECK (link_type IN ('fact', 'npc', 'map', 'location', 'module_entity')),
  link_id TEXT,
  version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
  created_by_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  revealed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  revealed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK ((link_type IS NULL) = (link_id IS NULL))
);

CREATE TABLE IF NOT EXISTS handout_read_receipts (
  handout_id TEXT NOT NULL REFERENCES campaign_handouts(id) ON DELETE CASCADE,
  member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE CASCADE,
  first_read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (handout_id, member_id)
);

CREATE INDEX IF NOT EXISTS idx_handouts_campaign
  ON campaign_handouts(campaign_id, status, pinned, created_at);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
