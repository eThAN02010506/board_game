"""Migration 56: versioned scenario contracts and immutable run bindings."""

import sqlite3

VERSION = 56
NAME = "scenario_contract_versions"

DDL = """
CREATE TABLE IF NOT EXISTS scenario_contract_versions (
  id TEXT PRIMARY KEY,
  module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  contract_key TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version > 0),
  schema_version INTEGER NOT NULL CHECK (schema_version > 0),
  source_version INTEGER NOT NULL CHECK (source_version > 0),
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'published', 'superseded')),
  row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
  contract_hash TEXT NOT NULL CHECK (length(contract_hash) = 64),
  contract_json TEXT NOT NULL CHECK (json_valid(contract_json)),
  validation_json TEXT NOT NULL CHECK (json_valid(validation_json)),
  created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  published_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  published_at TEXT,
  UNIQUE(module_id, version),
  UNIQUE(module_id, contract_hash)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scenario_contract_one_published
  ON scenario_contract_versions(module_id) WHERE status = 'published';
CREATE INDEX IF NOT EXISTS idx_scenario_contract_module_version
  ON scenario_contract_versions(module_id, version DESC);
CREATE TABLE IF NOT EXISTS module_run_contract_bindings (
  run_id TEXT PRIMARY KEY REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  contract_version_id TEXT NOT NULL
    REFERENCES scenario_contract_versions(id) ON DELETE RESTRICT,
  bound_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_module_run_contract_version
  ON module_run_contract_bindings(contract_version_id);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
