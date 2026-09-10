"""Migration 60: resumable background ScenarioContract compilation jobs."""

import sqlite3

VERSION = 60
NAME = "scenario_contract_compilation_jobs"

DDL = """
CREATE TABLE scenario_contract_jobs (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  run_id TEXT REFERENCES campaign_module_runs(id) ON DELETE SET NULL,
  ruleset_id TEXT NOT NULL,
  automation_level TEXT NOT NULL
    CHECK (automation_level IN ('conservative', 'balanced', 'ai_kp')),
  created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  source_fingerprint TEXT NOT NULL CHECK (length(source_fingerprint) = 64),
  contract_key TEXT NOT NULL,
  source_version INTEGER NOT NULL DEFAULT 1 CHECK (source_version > 0),
  title TEXT NOT NULL,
  corpus_block_count INTEGER NOT NULL CHECK (corpus_block_count > 0),
  corpus_total_block_count INTEGER NOT NULL CHECK (corpus_total_block_count > 0),
  corpus_truncated INTEGER NOT NULL DEFAULT 0 CHECK (corpus_truncated IN (0, 1)),
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed')),
  stage TEXT NOT NULL DEFAULT 'queued',
  progress_current INTEGER NOT NULL DEFAULT 0 CHECK (progress_current >= 0),
  progress_total INTEGER NOT NULL CHECK (progress_total > 0),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
  next_run_at TEXT,
  locked_by TEXT,
  locked_at TEXT,
  last_error TEXT,
  retryable INTEGER NOT NULL DEFAULT 1 CHECK (retryable IN (0, 1)),
  result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_scenario_contract_jobs_queue
  ON scenario_contract_jobs(status, next_run_at, created_at);
CREATE INDEX idx_scenario_contract_jobs_module
  ON scenario_contract_jobs(module_id, updated_at DESC);
CREATE UNIQUE INDEX idx_scenario_contract_one_active_job
  ON scenario_contract_jobs(module_id)
  WHERE status IN ('queued', 'running', 'retry_wait');

CREATE TABLE scenario_contract_job_partitions (
  job_id TEXT NOT NULL REFERENCES scenario_contract_jobs(id) ON DELETE CASCADE,
  partition_index INTEGER NOT NULL CHECK (partition_index >= 0),
  evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
  evidence_hash TEXT NOT NULL CHECK (length(evidence_hash) = 64),
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  model_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (model_attempt_count >= 0),
  validation_errors_json TEXT NOT NULL DEFAULT '[]'
    CHECK (json_valid(validation_errors_json)),
  result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
  last_error TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (job_id, partition_index)
);
CREATE INDEX idx_scenario_contract_partitions_status
  ON scenario_contract_job_partitions(job_id, status, partition_index);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
