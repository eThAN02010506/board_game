"""Migration 64: durable stepwise kernel task-method execution."""

import sqlite3

VERSION = 64
NAME = "durable_kernel_plans"

DDL = """
CREATE TABLE kernel_plan_instances (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  root_action_id TEXT NOT NULL UNIQUE REFERENCES player_actions(id) ON DELETE CASCADE,
  contract_hash TEXT NOT NULL CHECK (length(contract_hash) = 64),
  method_id TEXT NOT NULL,
  task_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'completed', 'failed', 'cancelled')),
  current_step_index INTEGER NOT NULL DEFAULT 0 CHECK (current_step_index >= 0),
  step_count INTEGER NOT NULL CHECK (step_count BETWEEN 1 AND 8),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE kernel_plan_steps (
  plan_id TEXT NOT NULL REFERENCES kernel_plan_instances(id) ON DELETE CASCADE,
  step_index INTEGER NOT NULL CHECK (step_index >= 0),
  step_id TEXT NOT NULL,
  operator_id TEXT NOT NULL,
  action_id TEXT UNIQUE REFERENCES player_actions(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'awaiting_resolution', 'committed', 'failed')),
  outcome TEXT,
  preview_hash TEXT CHECK (preview_hash IS NULL OR length(preview_hash) = 64),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (plan_id, step_index),
  UNIQUE (plan_id, step_id)
);
CREATE INDEX idx_kernel_plan_steps_action ON kernel_plan_steps(action_id);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
