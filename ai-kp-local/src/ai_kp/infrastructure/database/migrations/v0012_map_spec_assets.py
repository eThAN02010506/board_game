"""Migration 12: versioned map specifications and image assets."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 12
NAME = "add_versioned_map_specs_and_assets"


DDL = """
CREATE TABLE IF NOT EXISTS map_revisions (
  id TEXT PRIMARY KEY,
  map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
  revision_no INTEGER NOT NULL,
  spec_version TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  spec_hash TEXT NOT NULL,
  layout_hash TEXT NOT NULL,
  validation_json TEXT NOT NULL DEFAULT '{}',
  source_kind TEXT NOT NULL DEFAULT 'kp_brief',
  created_by TEXT NOT NULL DEFAULT 'ai',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(map_id, revision_no),
  UNIQUE(map_id, spec_hash)
);
CREATE TABLE IF NOT EXISTS map_assets (
  id TEXT PRIMARY KEY,
  map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
  revision_id TEXT NOT NULL REFERENCES map_revisions(id) ON DELETE CASCADE,
  audience TEXT NOT NULL DEFAULT 'table' CHECK (audience IN ('table', 'kp')),
  kind TEXT NOT NULL DEFAULT 'background' CHECK (kind IN ('background', 'thumbnail')),
  status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'failed')),
  generation_input_hash TEXT NOT NULL,
  content_hash TEXT,
  storage_path TEXT,
  mime_type TEXT,
  width INTEGER,
  height INTEGER,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  seed INTEGER,
  parameters_json TEXT NOT NULL DEFAULT '{}',
  prompt_text TEXT NOT NULL DEFAULT '',
  error_text TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(map_id, generation_input_hash)
);
CREATE INDEX IF NOT EXISTS idx_map_revisions_map
  ON map_revisions(map_id, revision_no);
CREATE INDEX IF NOT EXISTS idx_map_assets_map
  ON map_assets(map_id, created_at);
"""


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(connection, "maps", "current_revision_id", "TEXT")
    ensure_column(connection, "maps", "selected_public_asset_id", "TEXT")
    ensure_column(connection, "maps", "reveal_version", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(connection, "map_locations", "element_id", "TEXT")
    ensure_column(connection, "map_routes", "element_id", "TEXT")
    connection.execute("UPDATE map_locations SET element_id = id WHERE element_id IS NULL")
    connection.execute("UPDATE map_routes SET element_id = id WHERE element_id IS NULL")
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
