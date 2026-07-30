"""Add evidence-backed session recap draft and review records."""

import sqlite3

VERSION = 32
NAME = "add_session_recap_reviews"

DDL = """
CREATE TABLE IF NOT EXISTS session_recap_runs (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'completed')),
  event_window_hash TEXT NOT NULL CHECK (length(event_window_hash) = 64),
  event_ids_json TEXT NOT NULL CHECK (json_valid(event_ids_json)),
  generation_cutoff TEXT NOT NULL,
  source_model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  repaired INTEGER NOT NULL DEFAULT 0 CHECK (repaired IN (0, 1)),
  created_by_member_id TEXT NOT NULL
    REFERENCES session_members(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT,
  UNIQUE(session_id, event_window_hash)
);

CREATE TABLE IF NOT EXISTS session_recap_candidates (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES session_recap_runs(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  order_index INTEGER NOT NULL,
  text TEXT NOT NULL CHECK (length(trim(text)) BETWEEN 1 AND 2000),
  scope TEXT NOT NULL CHECK (
    scope IN (
      'campaign_fact', 'pc_major', 'pc_side', 'npc_interaction',
      'npc_relationship', 'location_fact', 'clue'
    )
  ),
  importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
  visibility TEXT NOT NULL CHECK (visibility IN ('player', 'table', 'kp')),
  pc_id TEXT REFERENCES player_characters(id) ON DELETE SET NULL,
  npc_id TEXT REFERENCES npcs(id) ON DELETE SET NULL,
  happened_at TEXT,
  source_event_ids_json TEXT NOT NULL CHECK (json_valid(source_event_ids_json)),
  rationale TEXT NOT NULL CHECK (length(trim(rationale)) BETWEEN 1 AND 2000),
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'approved', 'rejected')),
  review_payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(review_payload_json)),
  memory_id TEXT REFERENCES memories(id) ON DELETE SET NULL,
  reviewed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  reviewed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, order_index)
);

CREATE INDEX IF NOT EXISTS idx_session_recap_runs_session_created
  ON session_recap_runs(session_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_session_recap_candidates_draft
  ON session_recap_candidates(run_id, order_index)
  WHERE status = 'draft';
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
