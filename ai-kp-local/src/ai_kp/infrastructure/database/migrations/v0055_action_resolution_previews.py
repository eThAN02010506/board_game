"""Migration 55: immutable action-resolution previews for kernel shadow mode."""

import sqlite3

VERSION = 55
NAME = "action_resolution_previews"

DDL = """
CREATE TABLE IF NOT EXISTS action_resolution_previews (
  id TEXT PRIMARY KEY,
  action_id TEXT NOT NULL REFERENCES player_actions(id) ON DELETE CASCADE,
  run_id TEXT REFERENCES campaign_module_runs(id) ON DELETE SET NULL,
  source TEXT NOT NULL
    CHECK (source IN ('legacy_projection', 'kernel_shadow', 'kernel_authority')),
  contract_id TEXT NOT NULL,
  scenario_version INTEGER NOT NULL CHECK (scenario_version > 0),
  snapshot_version INTEGER NOT NULL CHECK (snapshot_version >= 0),
  operator_id TEXT NOT NULL,
  preview_hash TEXT NOT NULL CHECK (length(preview_hash) = 64),
  preview_json TEXT NOT NULL CHECK (json_valid(preview_json)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(action_id, source, snapshot_version, preview_hash)
);
CREATE INDEX IF NOT EXISTS idx_action_resolution_previews_action
  ON action_resolution_previews(action_id, created_at);
CREATE INDEX IF NOT EXISTS idx_action_resolution_previews_run
  ON action_resolution_previews(run_id, created_at);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
