"""Migration 43: approved dynamic-branch execution aggregates."""

import sqlite3

VERSION = 43
NAME = "add_dynamic_branch_runs"

DDL = """
CREATE TABLE dynamic_branch_runs (
  id TEXT PRIMARY KEY,
  proposal_id TEXT NOT NULL UNIQUE
    REFERENCES turn_proposals(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  module_run_id TEXT NOT NULL
    REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'approved'
    CHECK (status IN ('approved', 'active', 'paused', 'completed', 'abandoned')),
  current_beat_index INTEGER NOT NULL DEFAULT 0 CHECK (current_beat_index >= 0),
  plan_json TEXT NOT NULL CHECK (json_valid(plan_json)),
  version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
  created_by_member_id TEXT
    REFERENCES session_members(id) ON DELETE SET NULL,
  activated_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE dynamic_branch_events (
  id TEXT PRIMARY KEY,
  branch_id TEXT NOT NULL REFERENCES dynamic_branch_runs(id) ON DELETE CASCADE,
  event_seq INTEGER NOT NULL CHECK (event_seq >= 1),
  event_type TEXT NOT NULL
    CHECK (event_type IN (
      'approved', 'activated', 'beat_resolved', 'paused',
      'resumed', 'completed', 'abandoned'
    )),
  beat_id TEXT,
  outcome TEXT CHECK (
    outcome IS NULL OR outcome IN ('succeeded', 'failed', 'skipped')
  ),
  note TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload_json)),
  actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  aggregate_version INTEGER NOT NULL CHECK (aggregate_version >= 0),
  command_id TEXT NOT NULL CHECK (length(trim(command_id)) BETWEEN 8 AND 200),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(branch_id, event_seq),
  UNIQUE(branch_id, command_id)
);

CREATE INDEX idx_dynamic_branches_campaign
  ON dynamic_branch_runs(campaign_id, status, updated_at);
CREATE INDEX idx_dynamic_branch_events
  ON dynamic_branch_events(branch_id, event_seq);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
