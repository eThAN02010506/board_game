"""Migration 21: optimistic concurrency for campaign module runs."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 21
NAME = "add_module_run_version"


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(
        connection,
        "campaign_module_runs",
        "version",
        "INTEGER NOT NULL DEFAULT 0",
    )
