"""Add explicit investigator timeline branches and permanent change review."""

import sqlite3

VERSION = 33
NAME = "add_cross_campaign_character_timelines"

DDL = """
CREATE TABLE IF NOT EXISTS investigator_timeline_branches (
  id TEXT PRIMARY KEY,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  label TEXT NOT NULL CHECK (length(trim(label)) BETWEEN 1 AND 120),
  is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_investigator_timeline_primary
  ON investigator_timeline_branches(investigator_id)
  WHERE is_primary = 1;

CREATE INDEX IF NOT EXISTS idx_investigator_timeline_branches_owner
  ON investigator_timeline_branches(investigator_id, status, created_at);

CREATE TABLE IF NOT EXISTS investigator_campaign_participations (
  id TEXT PRIMARY KEY,
  branch_id TEXT NOT NULL
    REFERENCES investigator_timeline_branches(id) ON DELETE RESTRICT,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  approved_revision_id TEXT NOT NULL
    REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  legacy_pc_id TEXT REFERENCES player_characters(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'completed')),
  world_started_at TEXT,
  world_ended_at TEXT,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(session_id, investigator_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_investigator_participation_active_branch
  ON investigator_campaign_participations(branch_id)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_investigator_participation_history
  ON investigator_campaign_participations(investigator_id, started_at, id);

CREATE TABLE IF NOT EXISTS investigator_permanent_change_proposals (
  id TEXT PRIMARY KEY,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  branch_id TEXT NOT NULL
    REFERENCES investigator_timeline_branches(id) ON DELETE RESTRICT,
  base_revision_id TEXT NOT NULL
    REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  source_event_id TEXT NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
  kind TEXT NOT NULL CHECK (
    kind IN (
      'major_experience', 'scar', 'relationship', 'spell',
      'characteristic', 'skill'
    )
  ),
  summary TEXT NOT NULL CHECK (length(trim(summary)) BETWEEN 1 AND 1000),
  change_json TEXT NOT NULL CHECK (json_valid(change_json)),
  rationale TEXT NOT NULL CHECK (length(trim(rationale)) BETWEEN 1 AND 2000),
  status TEXT NOT NULL DEFAULT 'proposed'
    CHECK (status IN ('proposed', 'accepted', 'rejected')),
  proposed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  decided_by_profile_id TEXT REFERENCES player_profiles(id) ON DELETE SET NULL,
  decision_reason TEXT,
  decision_hash TEXT,
  resulting_revision_id TEXT
    REFERENCES investigator_revisions(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_investigator_permanent_changes_owner
  ON investigator_permanent_change_proposals(investigator_id, status, created_at);
"""


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def migrate(connection: sqlite3.Connection) -> None:
    if "origin_type" not in _columns(connection, "investigator_revisions"):
        connection.execute(
            "ALTER TABLE investigator_revisions "
            "ADD COLUMN origin_type TEXT NOT NULL DEFAULT 'player_edit' "
            "CHECK (origin_type IN ('player_edit', 'xlsx_import', 'milestone'))"
        )
        connection.execute(
            """
            UPDATE investigator_revisions
            SET origin_type = CASE
              WHEN source_type = 'xlsx' THEN 'xlsx_import'
              ELSE 'player_edit'
            END
            """
        )
    if "timeline_branch_id" not in _columns(connection, "campaign_investigators"):
        connection.execute(
            "ALTER TABLE campaign_investigators "
            "ADD COLUMN timeline_branch_id TEXT "
            "REFERENCES investigator_timeline_branches(id) ON DELETE RESTRICT"
        )
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
