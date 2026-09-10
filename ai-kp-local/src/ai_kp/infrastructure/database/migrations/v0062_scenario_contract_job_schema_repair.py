"""Migration 62: repair pre-release ScenarioContract job schema drift."""

import sqlite3

VERSION = 62
NAME = "scenario_contract_job_schema_repair"


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def migrate(connection: sqlite3.Connection) -> None:
    """Backfill columns missing from databases created by the v60 prototype."""

    job_columns = _columns(connection, "scenario_contract_jobs")
    if "retryable" not in job_columns:
        connection.execute(
            """
            ALTER TABLE scenario_contract_jobs
            ADD COLUMN retryable INTEGER NOT NULL DEFAULT 1
              CHECK (retryable IN (0, 1))
            """
        )

    partition_columns = _columns(connection, "scenario_contract_job_partitions")
    if "model_attempt_count" not in partition_columns:
        connection.execute(
            """
            ALTER TABLE scenario_contract_job_partitions
            ADD COLUMN model_attempt_count INTEGER NOT NULL DEFAULT 0
              CHECK (model_attempt_count >= 0)
            """
        )
    if "validation_errors_json" not in partition_columns:
        connection.execute(
            """
            ALTER TABLE scenario_contract_job_partitions
            ADD COLUMN validation_errors_json TEXT NOT NULL DEFAULT '[]'
              CHECK (json_valid(validation_errors_json))
            """
        )
