"""Persist player-owned, version-bound parallel action batches."""

from __future__ import annotations

import sqlite3

VERSION = 68
NAME = "parallel_action_batches"

SQL = """
CREATE TABLE IF NOT EXISTS parallel_action_batches (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  run_id TEXT NOT NULL REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
  contract_version_id TEXT NOT NULL
    REFERENCES scenario_contract_versions(id) ON DELETE RESTRICT,
  base_state_version INTEGER NOT NULL CHECK (base_state_version >= 0),
  idempotency_key TEXT NOT NULL
    CHECK (length(trim(idempotency_key)) BETWEEN 8 AND 200),
  action_set_hash TEXT NOT NULL CHECK (length(action_set_hash) = 64),
  preparation_hash TEXT NOT NULL CHECK (length(preparation_hash) = 64),
  status TEXT NOT NULL DEFAULT 'awaiting_confirmation' CHECK (
    status IN (
      'awaiting_confirmation', 'awaiting_checks', 'ready', 'committing',
      'settled', 'needs_attention', 'superseded'
    )
  ),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  attention_reason TEXT NOT NULL DEFAULT '' CHECK (length(attention_reason) <= 2000),
  settlement_hash TEXT CHECK (
    settlement_hash IS NULL OR length(settlement_hash) = 64
  ),
  scenario_command_batch_id TEXT
    REFERENCES scenario_command_batches(id) ON DELETE RESTRICT,
  created_by_member_id TEXT
    REFERENCES session_members(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  settled_at TEXT,
  UNIQUE(campaign_id, idempotency_key),
  UNIQUE(scenario_command_batch_id),
  CHECK (
    (status = 'settled' AND settlement_hash IS NOT NULL
      AND scenario_command_batch_id IS NOT NULL AND settled_at IS NOT NULL)
    OR
    (status != 'settled' AND settlement_hash IS NULL
      AND scenario_command_batch_id IS NULL AND settled_at IS NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_parallel_action_batches_run_status
  ON parallel_action_batches(run_id, status, created_at, id);

CREATE TABLE IF NOT EXISTS parallel_action_batch_items (
  batch_id TEXT NOT NULL
    REFERENCES parallel_action_batches(id) ON DELETE CASCADE,
  action_id TEXT NOT NULL UNIQUE
    REFERENCES player_actions(id) ON DELETE RESTRICT,
  proposal_id TEXT NOT NULL UNIQUE
    REFERENCES turn_proposals(id) ON DELETE RESTRICT,
  adjudication_id TEXT NOT NULL UNIQUE
    REFERENCES player_action_adjudications(id) ON DELETE RESTRICT,
  actor_id TEXT NOT NULL CHECK (length(trim(actor_id)) BETWEEN 1 AND 160),
  operator_id TEXT NOT NULL CHECK (length(trim(operator_id)) BETWEEN 1 AND 160),
  preview_hash TEXT NOT NULL CHECK (length(preview_hash) = 64),
  selected_skill_key TEXT CHECK (
    selected_skill_key IS NULL OR length(trim(selected_skill_key)) BETWEEN 1 AND 120
  ),
  outcome_key TEXT CHECK (
    outcome_key IS NULL OR length(trim(outcome_key)) BETWEEN 1 AND 120
  ),
  check_result_fingerprint TEXT CHECK (
    check_result_fingerprint IS NULL OR length(check_result_fingerprint) = 64
  ),
  priority INTEGER NOT NULL DEFAULT 0 CHECK (priority BETWEEN -1000 AND 1000),
  PRIMARY KEY (batch_id, action_id),
  UNIQUE(batch_id, proposal_id),
  UNIQUE(batch_id, adjudication_id),
  UNIQUE(batch_id, actor_id)
);

CREATE INDEX IF NOT EXISTS idx_parallel_action_batch_items_action
  ON parallel_action_batch_items(action_id, batch_id);

CREATE TABLE IF NOT EXISTS parallel_action_batch_events (
  id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL
    REFERENCES parallel_action_batches(id) ON DELETE CASCADE,
  version INTEGER NOT NULL CHECK (version > 0),
  event_type TEXT NOT NULL CHECK (
    event_type IN (
      'created', 'item_updated',
      'confirmations_completed_with_checks',
      'confirmations_completed_without_checks', 'checks_completed',
      'begin_commit', 'commit_succeeded', 'request_attention', 'supersede',
      'resume_confirmations', 'resume_checks', 'resume_ready'
    )
  ),
  actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
  payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload_json)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(batch_id, version)
);

CREATE INDEX IF NOT EXISTS idx_parallel_action_batch_events_batch
  ON parallel_action_batch_events(batch_id, version, created_at, id);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in SQL.split(";"):
        if statement.strip():
            connection.execute(statement)


__all__ = ["NAME", "SQL", "VERSION", "migrate"]
