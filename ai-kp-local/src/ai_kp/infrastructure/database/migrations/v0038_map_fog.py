"""Migration 38: versioned map fog regions."""

import sqlite3

VERSION = 38
NAME = "add_map_fog_regions"

DDL = """
CREATE TABLE IF NOT EXISTS map_fog_regions (
  id TEXT PRIMARY KEY,
  map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
  revision_id TEXT NOT NULL REFERENCES map_revisions(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  polygon_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'hidden'
    CHECK (status IN ('hidden', 'revealed')),
  version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
  created_by_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_map_fog_regions
  ON map_fog_regions(map_id, revision_id, status);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
