"""Persist consent-bound regrouping after a parallel action revision."""

import sqlite3

VERSION = 71
NAME = "parallel_action_regathers"


def migrate(connection: sqlite3.Connection) -> None:
    sql = """
        CREATE TABLE IF NOT EXISTS parallel_action_regathers (
          id TEXT PRIMARY KEY,
          source_batch_id TEXT NOT NULL UNIQUE
            REFERENCES parallel_action_batches(id) ON DELETE CASCADE,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          session_id TEXT NOT NULL
            REFERENCES campaign_sessions(id) ON DELETE CASCADE,
          source_run_id TEXT NOT NULL
            REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
          source_module_run_version INTEGER NOT NULL CHECK (
            source_module_run_version >= 0
          ),
          source_state_version INTEGER NOT NULL CHECK (source_state_version >= 0),
          status TEXT NOT NULL DEFAULT 'gathering' CHECK (
            status IN ('gathering', 'queued', 'completed', 'cancelled')
          ),
          version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
          prepare_job_id TEXT UNIQUE
            REFERENCES auto_kp_jobs(id) ON DELETE SET NULL,
          resulting_batch_id TEXT UNIQUE
            REFERENCES parallel_action_batches(id) ON DELETE SET NULL,
          completion_kind TEXT CHECK (
            completion_kind IS NULL OR completion_kind IN ('batch', 'fallback')
          ),
          created_by_member_id TEXT
            REFERENCES session_members(id) ON DELETE SET NULL,
          reason TEXT NOT NULL DEFAULT '' CHECK (length(reason) <= 2000),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at TEXT,
          CHECK (
            (status = 'completed' AND completion_kind IS NOT NULL
              AND completed_at IS NOT NULL)
            OR
            (status != 'completed' AND completion_kind IS NULL
              AND resulting_batch_id IS NULL AND completed_at IS NULL)
          )
        );
        CREATE INDEX IF NOT EXISTS idx_parallel_action_regathers_active
          ON parallel_action_regathers(
            campaign_id, session_id, status, updated_at, id
          );

        CREATE TABLE IF NOT EXISTS parallel_action_regather_members (
          regather_id TEXT NOT NULL
            REFERENCES parallel_action_regathers(id) ON DELETE CASCADE,
          member_id TEXT NOT NULL
            REFERENCES session_members(id) ON DELETE RESTRICT,
          prior_action_id TEXT NOT NULL
            REFERENCES player_actions(id) ON DELETE RESTRICT,
          replacement_action_id TEXT UNIQUE
            REFERENCES player_actions(id) ON DELETE RESTRICT,
          auto_kp_requested INTEGER NOT NULL DEFAULT 0 CHECK (
            auto_kp_requested IN (0, 1)
          ),
          submitted_at TEXT,
          PRIMARY KEY (regather_id, member_id),
          UNIQUE(regather_id, prior_action_id),
          CHECK (
            (replacement_action_id IS NULL AND auto_kp_requested = 0
              AND submitted_at IS NULL)
            OR
            (replacement_action_id IS NOT NULL AND submitted_at IS NOT NULL)
          )
        );
        CREATE INDEX IF NOT EXISTS idx_parallel_action_regather_member_active
          ON parallel_action_regather_members(member_id, regather_id);

        CREATE TABLE IF NOT EXISTS parallel_action_regather_events (
          id TEXT PRIMARY KEY,
          regather_id TEXT NOT NULL
            REFERENCES parallel_action_regathers(id) ON DELETE CASCADE,
          version INTEGER NOT NULL CHECK (version > 0),
          event_type TEXT NOT NULL CHECK (
            event_type IN (
              'created', 'member_submitted', 'queued', 'reopened',
              'completed', 'cancelled'
            )
          ),
          actor_member_id TEXT
            REFERENCES session_members(id) ON DELETE SET NULL,
          payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload_json)),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(regather_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_parallel_action_regather_events
          ON parallel_action_regather_events(regather_id, version, id);
        """
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(statement)
