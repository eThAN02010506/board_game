"""Persist the player's decision to accept a failed check without pushing."""

from __future__ import annotations

import sqlite3

VERSION = 67
NAME = "skill_check_push_decisions"

SQL = """
CREATE TABLE IF NOT EXISTS skill_check_push_decisions (
  id TEXT PRIMARY KEY,
  check_id TEXT NOT NULL UNIQUE REFERENCES skill_checks(id) ON DELETE CASCADE,
  actor_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  decision TEXT NOT NULL CHECK (decision = 'accept_failure'),
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(SQL)


__all__ = ["NAME", "SQL", "VERSION", "migrate"]
