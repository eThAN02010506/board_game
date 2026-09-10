"""Migration 58: immutable run-scoped scenario contract overlays."""

import sqlite3

VERSION = 58
NAME = "scenario_contract_overlays"

DDL = """
CREATE TABLE IF NOT EXISTS scenario_contract_overlays (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES scenario_run_states(run_id) ON DELETE CASCADE,
  proposal_key TEXT NOT NULL,
  sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
  status TEXT NOT NULL CHECK (status IN ('review_required', 'active', 'rejected')),
  base_contract_hash TEXT NOT NULL CHECK (length(base_contract_hash) = 64),
  base_state_version INTEGER NOT NULL CHECK (base_state_version >= 0),
  merged_contract_hash TEXT CHECK (
    merged_contract_hash IS NULL OR length(merged_contract_hash) = 64
  ),
  proposal_json TEXT NOT NULL CHECK (json_valid(proposal_json)),
  decision_json TEXT NOT NULL CHECK (json_valid(decision_json)),
  merged_contract_json TEXT CHECK (
    merged_contract_json IS NULL OR json_valid(merged_contract_json)
  ),
  created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  reviewed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  activated_at TEXT,
  UNIQUE(run_id, proposal_key),
  UNIQUE(run_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_scenario_contract_overlays_run
  ON scenario_contract_overlays(run_id, sequence_no);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
