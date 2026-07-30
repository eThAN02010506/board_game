"""Migration 39: replayable simulated-campaign evaluation cases and runs."""

import sqlite3

VERSION = 39
NAME = "add_simulated_campaign_evaluations"

DDL = """
CREATE TABLE IF NOT EXISTS simulation_cases (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  definition_json TEXT NOT NULL,
  definition_hash TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_by_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (campaign_id, name, version)
);
CREATE TABLE IF NOT EXISTS simulation_runs (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES simulation_cases(id) ON DELETE CASCADE,
  definition_hash TEXT NOT NULL,
  runner_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('passed', 'failed', 'error')),
  metrics_json TEXT NOT NULL,
  trajectory_json TEXT NOT NULL,
  result_fingerprint TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_simulation_runs_case
  ON simulation_runs(case_id, created_at);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
