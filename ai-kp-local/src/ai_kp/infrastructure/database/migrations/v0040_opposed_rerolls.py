"""Migration 40: opposed-check reroll lineage."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 40
NAME = "add_opposed_check_reroll_lineage"


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(
        connection,
        "opposed_checks",
        "rerolled_from_opposed_check_id",
        "TEXT REFERENCES opposed_checks(id) ON DELETE SET NULL",
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_opposed_checks_rerolled_from
        ON opposed_checks(rerolled_from_opposed_check_id)
        WHERE rerolled_from_opposed_check_id IS NOT NULL
        """
    )
