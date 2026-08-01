"""Migration 49: durable Auto KP background job state machine."""

import sqlite3

VERSION = 49
NAME = "add_auto_kp_jobs"


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS auto_kp_jobs (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          run_id TEXT REFERENCES campaign_module_runs(id) ON DELETE SET NULL,
          job_type TEXT NOT NULL
            CHECK (job_type IN (
              'player_action',
              'parallel_actions',
              'world_expansion',
              'check_consequence'
            )),
          resource_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued'
            CHECK (status IN (
              'queued',
              'running',
              'retry_wait',
              'succeeded',
              'failed',
              'needs_attention',
              'cancelled'
            )),
          stage TEXT NOT NULL DEFAULT 'queued',
          attempt_count INTEGER NOT NULL DEFAULT 0,
          max_attempts INTEGER NOT NULL DEFAULT 3,
          next_run_at TEXT,
          locked_by TEXT,
          locked_at TEXT,
          last_error TEXT,
          payload_json TEXT NOT NULL DEFAULT '{}',
          result_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(campaign_id, idempotency_key)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_auto_kp_jobs_campaign_status
        ON auto_kp_jobs(campaign_id, status, updated_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_auto_kp_jobs_runnable
        ON auto_kp_jobs(status, next_run_at, created_at)
        """
    )
