"""Migration 22: durable generations for knowledge-extraction claims."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 22
NAME = "add_knowledge_extraction_attempts"


def migrate(connection: sqlite3.Connection) -> None:
    for table in ("rule_chunks", "module_chunks"):
        ensure_column(
            connection,
            table,
            "attempt_count",
            "INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0)",
        )
