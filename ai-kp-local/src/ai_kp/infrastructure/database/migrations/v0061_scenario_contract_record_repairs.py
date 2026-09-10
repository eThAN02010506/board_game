"""Migration 61: persist bounded ScenarioContract record-repair diagnostics."""

import sqlite3

VERSION = 61
NAME = "scenario_contract_record_repairs"


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        ALTER TABLE scenario_contract_job_partitions
        ADD COLUMN repair_diagnostics_json TEXT NOT NULL DEFAULT '[]'
          CHECK (json_valid(repair_diagnostics_json))
        """
    )
