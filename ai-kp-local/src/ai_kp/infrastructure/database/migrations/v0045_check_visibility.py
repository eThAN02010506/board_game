"""Migration 45: explicit skill-check visibility modes."""

import sqlite3

VERSION = 45
NAME = "add_skill_check_visibility_modes"


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        ALTER TABLE skill_checks
        ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'
          CHECK (visibility IN ('public', 'private', 'blind'))
        """
    )
    connection.execute(
        """
        UPDATE skill_checks
        SET visibility = CASE WHEN hidden = 1 THEN 'blind' ELSE 'public' END
        """
    )
