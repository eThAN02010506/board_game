"""Migration 6: investigator library."""

import sqlite3


VERSION = 6
NAME = "add_player_owned_investigator_library"


DDL = """
CREATE TABLE IF NOT EXISTS player_profiles (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS investigators (
  id TEXT PRIMARY KEY,
  owner_profile_id TEXT NOT NULL REFERENCES player_profiles(id) ON DELETE CASCADE,
  ruleset_id TEXT NOT NULL DEFAULT 'coc7-keeper-cn-2002c',
  name TEXT NOT NULL,
  current_revision_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  archived_at TEXT
);

CREATE TABLE IF NOT EXISTS investigator_revisions (
  id TEXT PRIMARY KEY,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  revision_no INTEGER NOT NULL,
  canonical_json TEXT NOT NULL,
  public_summary_json TEXT NOT NULL DEFAULT '{}',
  source_type TEXT NOT NULL CHECK (source_type IN ('manual', 'xlsx')),
  source_hash TEXT,
  template_id TEXT,
  parser_version TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(investigator_id, revision_no)
);

CREATE TABLE IF NOT EXISTS character_imports (
  id TEXT PRIMARY KEY,
  owner_profile_id TEXT NOT NULL REFERENCES player_profiles(id) ON DELETE CASCADE,
  investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
  revision_id TEXT NOT NULL REFERENCES investigator_revisions(id) ON DELETE CASCADE,
  source_filename TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  template_id TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_investigators_owner
  ON investigators(owner_profile_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_investigator_revisions_character
  ON investigator_revisions(investigator_id, revision_no);
CREATE INDEX IF NOT EXISTS idx_character_imports_owner
  ON character_imports(owner_profile_id, created_at);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
