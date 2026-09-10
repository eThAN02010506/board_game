"""Migration 63: durable source-coverage supplement partitions."""

import sqlite3

VERSION = 63
NAME = "scenario_contract_coverage_supplements"

DDL = """
CREATE TABLE scenario_contract_job_supplements (
  job_id TEXT NOT NULL REFERENCES scenario_contract_jobs(id) ON DELETE CASCADE,
  supplement_index INTEGER NOT NULL CHECK (supplement_index >= 0),
  evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
  payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
  coverage_targets_json TEXT NOT NULL CHECK (json_valid(coverage_targets_json)),
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  model_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (model_attempt_count >= 0),
  validation_errors_json TEXT NOT NULL DEFAULT '[]'
    CHECK (json_valid(validation_errors_json)),
  repair_diagnostics_json TEXT NOT NULL DEFAULT '[]'
    CHECK (json_valid(repair_diagnostics_json)),
  result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
  last_error TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (job_id, supplement_index)
);
CREATE INDEX idx_scenario_contract_supplements_status
  ON scenario_contract_job_supplements(job_id, status, supplement_index);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
