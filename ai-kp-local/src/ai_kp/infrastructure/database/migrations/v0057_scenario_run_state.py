"""Migration 57: authoritative scenario snapshots and command-batch receipts."""

import sqlite3

VERSION = 57
NAME = "scenario_run_state"

DDL = """
CREATE TABLE IF NOT EXISTS scenario_run_states (
  run_id TEXT PRIMARY KEY REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  contract_version_id TEXT NOT NULL
    REFERENCES scenario_contract_versions(id) ON DELETE RESTRICT,
  state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
  snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS scenario_command_batches (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES scenario_run_states(run_id) ON DELETE CASCADE,
  batch_kind TEXT NOT NULL
    CHECK (batch_kind IN ('action', 'parallel', 'reactive', 'plan_step', 'world_expansion')),
  idempotency_key TEXT NOT NULL,
  expected_version INTEGER NOT NULL CHECK (expected_version >= 0),
  result_version INTEGER NOT NULL CHECK (result_version = expected_version + 1),
  preview_hash TEXT NOT NULL CHECK (length(preview_hash) = 64),
  preview_json TEXT NOT NULL CHECK (json_valid(preview_json)),
  commands_json TEXT NOT NULL CHECK (json_valid(commands_json)),
  snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, idempotency_key),
  UNIQUE(run_id, result_version)
);
CREATE INDEX IF NOT EXISTS idx_scenario_command_batches_run
  ON scenario_command_batches(run_id, result_version);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
