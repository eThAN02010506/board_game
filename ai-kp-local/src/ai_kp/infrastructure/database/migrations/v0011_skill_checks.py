"""Migration 11: skill checks."""

import sqlite3

VERSION = 11
NAME = "add_replayable_skill_checks"


DDL = """
CREATE TABLE IF NOT EXISTS skill_checks (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  proposal_id TEXT REFERENCES turn_proposals(id) ON DELETE SET NULL,
  player_action_id TEXT REFERENCES player_actions(id) ON DELETE SET NULL,
  requested_by_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  roller_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  pc_id TEXT REFERENCES player_characters(id) ON DELETE SET NULL,
  investigator_id TEXT REFERENCES investigators(id) ON DELETE SET NULL,
  skill_key TEXT NOT NULL,
  skill_name TEXT NOT NULL,
  target INTEGER NOT NULL CHECK (target BETWEEN 0 AND 100),
  target_source TEXT NOT NULL,
  difficulty TEXT NOT NULL CHECK (difficulty IN ('regular', 'hard', 'extreme')),
  bonus_dice INTEGER NOT NULL DEFAULT 0 CHECK (bonus_dice BETWEEN -2 AND 2),
  hidden INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
  allow_push INTEGER NOT NULL DEFAULT 1 CHECK (allow_push IN (0, 1)),
  pushed_from_check_id TEXT REFERENCES skill_checks(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'requested'
    CHECK (status IN ('requested', 'resolved', 'overridden', 'cancelled')),
  input_method TEXT CHECK (input_method IN ('digital', 'physical')),
  raw_dice_json TEXT,
  selected_roll INTEGER CHECK (selected_roll BETWEEN 1 AND 100),
  threshold INTEGER,
  success_level TEXT
    CHECK (success_level IN ('fumble', 'failure', 'regular', 'hard', 'extreme', 'critical')),
  passed INTEGER CHECK (passed IN (0, 1)),
  ruleset_id TEXT NOT NULL,
  ruleset_version TEXT NOT NULL,
  source_reference_json TEXT NOT NULL,
  investigator_state_version INTEGER,
  resolved_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  override_reason TEXT,
  original_result_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS skill_check_actions (
  id TEXT PRIMARY KEY,
  check_id TEXT NOT NULL REFERENCES skill_checks(id) ON DELETE CASCADE,
  action_type TEXT NOT NULL
    CHECK (action_type IN ('requested', 'resolved', 'overridden', 'cancelled', 'pushed')),
  actor_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  reason TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_skill_checks_campaign
  ON skill_checks(campaign_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_skill_checks_roller
  ON skill_checks(roller_member_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_skill_checks_proposal
  ON skill_checks(proposal_id, created_at);
CREATE INDEX IF NOT EXISTS idx_skill_check_actions_check
  ON skill_check_actions(check_id, created_at);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
