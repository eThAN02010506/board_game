"""Migration 66: persist the player-confirmed plan attached to every check."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 66
NAME = "add_check_plans"


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(
        connection,
        "skill_checks",
        "check_plan_json",
        "TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(check_plan_json))",
    )
