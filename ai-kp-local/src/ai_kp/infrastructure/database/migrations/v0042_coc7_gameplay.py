"""Migration 42: replayable CoC7 encounter and character state transitions."""

import sqlite3

VERSION = 42
NAME = "add_coc7_gameplay_state_machines"

DDL = """
ALTER TABLE investigator_campaign_state
  ADD COLUMN daily_san_loss INTEGER NOT NULL DEFAULT 0 CHECK (daily_san_loss >= 0);
ALTER TABLE investigator_campaign_state
  ADD COLUMN daily_san_start INTEGER CHECK (daily_san_start BETWEEN 0 AND 99);
ALTER TABLE investigator_campaign_state
  ADD COLUMN last_san_day TEXT;

CREATE TABLE coc7_encounters (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('combat', 'chase')),
  title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'completed', 'cancelled')),
  round_no INTEGER NOT NULL DEFAULT 1 CHECK (round_no >= 1),
  turn_index INTEGER NOT NULL DEFAULT 0 CHECK (turn_index >= 0),
  state_json TEXT NOT NULL CHECK (json_valid(state_json)),
  version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
  created_by_member_id TEXT NOT NULL
    REFERENCES session_members(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT
);

CREATE TABLE coc7_gameplay_events (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  encounter_id TEXT REFERENCES coc7_encounters(id) ON DELETE CASCADE,
  investigator_id TEXT REFERENCES investigators(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL CHECK (length(trim(event_type)) BETWEEN 1 AND 100),
  actor_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  command_id TEXT NOT NULL CHECK (length(trim(command_id)) BETWEEN 8 AND 200),
  visibility TEXT NOT NULL DEFAULT 'table'
    CHECK (visibility IN ('table', 'kp', 'player')),
  input_json TEXT NOT NULL CHECK (json_valid(input_json)),
  result_json TEXT NOT NULL CHECK (json_valid(result_json)),
  aggregate_version INTEGER NOT NULL CHECK (aggregate_version >= 0),
  ruleset_id TEXT NOT NULL,
  ruleset_version TEXT NOT NULL,
  source_reference_json TEXT NOT NULL CHECK (json_valid(source_reference_json)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (session_id, command_id),
  CHECK (
    (encounter_id IS NOT NULL AND investigator_id IS NULL)
    OR (encounter_id IS NULL AND investigator_id IS NOT NULL)
  )
);

CREATE INDEX idx_coc7_encounters_campaign
  ON coc7_encounters(campaign_id, session_id, status, updated_at);
CREATE INDEX idx_coc7_gameplay_events_encounter
  ON coc7_gameplay_events(encounter_id, aggregate_version, created_at);
CREATE INDEX idx_coc7_gameplay_events_investigator
  ON coc7_gameplay_events(campaign_id, investigator_id, created_at);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
