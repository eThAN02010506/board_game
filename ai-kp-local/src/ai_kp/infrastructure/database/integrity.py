"""Integrity checks that cover SQLite structures omitted by PRAGMA checks."""

from __future__ import annotations

import sqlite3

_FTS_CHECKS = (
    (
        "memories_fts",
        (
            "INSERT INTO memories_fts(memories_fts, rank) "
            "VALUES('integrity-check', 1)"
        ),
    ),
    (
        "module_search_fts",
        (
            "INSERT INTO module_search_fts(module_search_fts) "
            "VALUES('integrity-check')"
        ),
    ),
)


def fts_integrity_issues(connection: sqlite3.Connection) -> list[dict[str, str]]:
    """Run FTS5's own checksum verification for every installed index."""

    installed = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    issues: list[dict[str, str]] = []
    for table, statement in _FTS_CHECKS:
        if table not in installed:
            continue
        try:
            connection.execute(statement)
        except sqlite3.DatabaseError as exc:
            issues.append({"table": table, "error": str(exc)[:1000]})
    return issues


__all__ = ["fts_integrity_issues"]
