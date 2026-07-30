"""Migration 35: persisted opposed checks."""

import sqlite3

VERSION = 35
NAME = "add_persisted_opposed_checks"

DDL = """
CREATE TABLE IF NOT EXISTS opposed_checks (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  requested_by_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  left_check_id TEXT NOT NULL UNIQUE REFERENCES skill_checks(id) ON DELETE RESTRICT,
  right_check_id TEXT NOT NULL UNIQUE REFERENCES skill_checks(id) ON DELETE RESTRICT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'resolved', 'reroll_required', 'cancelled')),
  result_json TEXT,
  result_fingerprint TEXT,
  resolved_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (left_check_id <> right_check_id)
);

CREATE TABLE IF NOT EXISTS opposed_check_actions (
  id TEXT PRIMARY KEY,
  opposed_check_id TEXT NOT NULL REFERENCES opposed_checks(id) ON DELETE CASCADE,
  action_type TEXT NOT NULL
    CHECK (action_type IN ('requested', 'resolved', 'reroll_required', 'cancelled')),
  actor_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_opposed_checks_campaign
  ON opposed_checks(campaign_id, session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_opposed_check_actions
  ON opposed_check_actions(opposed_check_id, created_at);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
