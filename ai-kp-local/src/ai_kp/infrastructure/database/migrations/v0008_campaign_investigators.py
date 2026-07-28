"""Migration 8: campaign investigators."""

import sqlite3

VERSION = 8
NAME = "add_campaign_investigator_review_and_runtime_state"


DDL = """
CREATE TABLE IF NOT EXISTS campaign_investigators (
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  owner_profile_id TEXT NOT NULL REFERENCES player_profiles(id) ON DELETE CASCADE,
  submitted_revision_id TEXT REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  approved_revision_id TEXT REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  legacy_pc_id TEXT REFERENCES player_characters(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'submitted', 'changes_requested', 'approved', 'withdrawn')),
  review_comment TEXT,
  reviewed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  reviewed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (campaign_id, investigator_id)
);

CREATE TABLE IF NOT EXISTS character_reviews (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  revision_id TEXT NOT NULL REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  action TEXT NOT NULL
    CHECK (action IN ('submitted', 'changes_requested', 'approved', 'withdrawn')),
  actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  actor_profile_id TEXT REFERENCES player_profiles(id) ON DELETE SET NULL,
  comment TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS investigator_campaign_state (
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  approved_revision_id TEXT NOT NULL REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  current_hp INTEGER NOT NULL,
  current_san INTEGER NOT NULL,
  current_mp INTEGER NOT NULL,
  current_luck INTEGER NOT NULL,
  conditions_json TEXT NOT NULL DEFAULT '[]',
  inventory_delta_json TEXT NOT NULL DEFAULT '{}',
  state_version INTEGER NOT NULL DEFAULT 0,
  current_game_time TEXT,
  last_event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (campaign_id, investigator_id)
);

CREATE INDEX IF NOT EXISTS idx_campaign_investigators_status
  ON campaign_investigators(campaign_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_campaign_investigators_owner
  ON campaign_investigators(owner_profile_id, campaign_id);
CREATE INDEX IF NOT EXISTS idx_character_reviews_target
  ON character_reviews(campaign_id, investigator_id, created_at);
CREATE INDEX IF NOT EXISTS idx_session_members_player_profile
  ON session_members(player_profile_id, campaign_id);
"""


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def migrate(connection: sqlite3.Connection) -> None:
    if "warnings_json" not in _columns(connection, "investigator_revisions"):
        connection.execute(
            "ALTER TABLE investigator_revisions "
            "ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "player_profile_id" not in _columns(connection, "session_members"):
        connection.execute(
            "ALTER TABLE session_members "
            "ADD COLUMN player_profile_id TEXT REFERENCES player_profiles(id) ON DELETE SET NULL"
        )
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
