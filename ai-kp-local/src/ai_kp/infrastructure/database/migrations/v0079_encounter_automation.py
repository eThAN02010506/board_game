"""Migration 79: restart-safe encounter automation jobs and checkpoints."""

import sqlite3

VERSION = 79
NAME = "encounter_automation"


def migrate(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError("migration 79 requires foreign keys disabled")
    table_sql = str(
        connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = 'auto_kp_jobs'"
        ).fetchone()[0]
    )
    if "'encounter_turn'" not in table_sql:
        preserved = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT sql FROM sqlite_schema
                WHERE tbl_name = 'auto_kp_jobs'
                  AND type IN ('index', 'trigger') AND sql IS NOT NULL
                ORDER BY type, name
                """
            ).fetchall()
        ]
        connection.execute(
            """
            CREATE TABLE auto_kp_jobs_v79 (
              id TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
              run_id TEXT REFERENCES campaign_module_runs(id) ON DELETE SET NULL,
              job_type TEXT NOT NULL CHECK (job_type IN (
                'player_action', 'parallel_actions', 'world_expansion',
                'check_consequence', 'encounter_turn'
              )),
              resource_id TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
                'queued', 'running', 'retry_wait', 'succeeded', 'failed',
                'needs_attention', 'cancelled'
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
            INSERT INTO auto_kp_jobs_v79
            SELECT * FROM auto_kp_jobs
            """
        )
        connection.execute("DROP TABLE auto_kp_jobs")
        connection.execute("ALTER TABLE auto_kp_jobs_v79 RENAME TO auto_kp_jobs")
        for statement in preserved:
            connection.execute(statement)

    statements = (
        """
        CREATE TABLE IF NOT EXISTS encounter_automation_turns (
          id TEXT PRIMARY KEY,
          encounter_id TEXT NOT NULL REFERENCES coc7_encounters(id) ON DELETE CASCADE,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
          encounter_version INTEGER NOT NULL CHECK (encounter_version >= 0),
          participant_id TEXT NOT NULL,
          phase TEXT NOT NULL CHECK (phase IN ('enemy', 'idle_player')),
          policy TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN (
            'planned', 'checkpointed', 'committed', 'needs_attention', 'cancelled'
          )),
          selection_json TEXT NOT NULL DEFAULT '{}',
          prepared_command_json TEXT,
          result_json TEXT,
          version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          committed_at TEXT,
          UNIQUE(encounter_id, encounter_version, participant_id, phase)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_encounter_automation_active
          ON encounter_automation_turns(encounter_id, status, updated_at)
        """,
    )
    for statement in statements:
        connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
