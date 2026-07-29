"""Migration 23: enforce active session identity and PC assignment uniqueness."""

import sqlite3

VERSION = 23
NAME = "enforce_session_assignment_uniqueness"

_DUPLICATE_CHECKS = (
    (
        "active player members share one player profile",
        """
        SELECT session_id, player_profile_id, COUNT(*) AS duplicate_count
        FROM session_members
        WHERE role = 'player' AND revoked_at IS NULL
          AND player_profile_id IS NOT NULL
        GROUP BY session_id, player_profile_id
        HAVING COUNT(*) > 1
        LIMIT 1
        """,
    ),
    (
        "active session members share one PC",
        """
        SELECT session_id, pc_id, COUNT(*) AS duplicate_count
        FROM session_members
        WHERE revoked_at IS NULL AND pc_id IS NOT NULL
        GROUP BY session_id, pc_id
        HAVING COUNT(*) > 1
        LIMIT 1
        """,
    ),
    (
        "claimed seats share one player profile",
        """
        SELECT session_id, player_profile_id, COUNT(*) AS duplicate_count
        FROM session_seats
        WHERE status = 'claimed' AND player_profile_id IS NOT NULL
        GROUP BY session_id, player_profile_id
        HAVING COUNT(*) > 1
        LIMIT 1
        """,
    ),
    (
        "active seats share one PC",
        """
        SELECT session_id, assigned_pc_id, COUNT(*) AS duplicate_count
        FROM session_seats
        WHERE status != 'revoked' AND assigned_pc_id IS NOT NULL
        GROUP BY session_id, assigned_pc_id
        HAVING COUNT(*) > 1
        LIMIT 1
        """,
    ),
)

_INDEX_DDL = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_session_members_active_profile
      ON session_members(session_id, player_profile_id)
      WHERE role = 'player' AND revoked_at IS NULL
        AND player_profile_id IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_session_members_active_pc
      ON session_members(session_id, pc_id)
      WHERE revoked_at IS NULL AND pc_id IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_session_seats_claimed_profile
      ON session_seats(session_id, player_profile_id)
      WHERE status = 'claimed' AND player_profile_id IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_session_seats_active_pc
      ON session_seats(session_id, assigned_pc_id)
      WHERE status != 'revoked' AND assigned_pc_id IS NOT NULL
    """,
)


def migrate(connection: sqlite3.Connection) -> None:
    conflicts = []
    for label, query in _DUPLICATE_CHECKS:
        row = connection.execute(query).fetchone()
        if row is not None:
            conflicts.append(
                f"{label}: session={row[0]!r}, value={row[1]!r}, count={row[2]}"
            )
    if conflicts:
        raise RuntimeError(
            "cannot enforce session assignment uniqueness until duplicate live "
            f"rows are resolved ({'; '.join(conflicts)})"
        )
    for statement in _INDEX_DDL:
        connection.execute(statement)
