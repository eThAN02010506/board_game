"""Migration 83: persist parser-owned section ancestry for module evidence."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 83
NAME = "module_chunk_section_ancestry"


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(connection, "module_chunks", "heading_level", "INTEGER")
    ensure_column(
        connection,
        "module_chunks",
        "section_path_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )


__all__ = ["NAME", "VERSION", "migrate"]
