"""Add durable play episodes and isolated continuity snapshots."""

import sqlite3

VERSION = 75
NAME = "session_continuity"


def migrate(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS campaign_episodes (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
          sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
          status TEXT NOT NULL CHECK (
            status IN ('prepared', 'in_progress', 'paused', 'ended')
          ),
          client_continue_id TEXT,
          event_start_rowid INTEGER NOT NULL CHECK (event_start_rowid > 0),
          started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          ended_at TEXT,
          version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
          UNIQUE(session_id, sequence_no),
          UNIQUE(session_id, client_continue_id)
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_campaign_episodes_current
          ON campaign_episodes(session_id)
          WHERE status IN ('prepared', 'in_progress', 'paused')
        """,
        """
        CREATE TABLE IF NOT EXISTS session_continuity_snapshots (
          id TEXT PRIMARY KEY,
          episode_id TEXT NOT NULL UNIQUE
            REFERENCES campaign_episodes(id) ON DELETE CASCADE,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
          client_end_id TEXT NOT NULL,
          event_window_hash TEXT NOT NULL CHECK (length(event_window_hash) = 64),
          event_ids_json TEXT NOT NULL CHECK (json_valid(event_ids_json)),
          generation_cutoff TEXT NOT NULL,
          public_projection_json TEXT NOT NULL CHECK (json_valid(public_projection_json)),
          observer_projection_json TEXT NOT NULL CHECK (json_valid(observer_projection_json)),
          kp_projection_json TEXT NOT NULL CHECK (json_valid(kp_projection_json)),
          ended_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(session_id, client_end_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_continuity_snapshots_campaign_created
          ON session_continuity_snapshots(campaign_id, created_at DESC, id DESC)
        """,
    )
    for statement in statements:
        connection.execute(statement)
    sessions = connection.execute(
        """
        SELECT id, campaign_id, status, started_at, ended_at
        FROM campaign_sessions
        WHERE NOT EXISTS (
          SELECT 1 FROM campaign_episodes episode WHERE episode.session_id = campaign_sessions.id
        )
        """
    ).fetchall()
    for row in sessions:
        connection.execute(
            """
            INSERT INTO campaign_episodes
              (id, campaign_id, session_id, sequence_no, status, event_start_rowid,
               started_at, ended_at)
            VALUES (?, ?, ?, 1, ?, 1, ?, ?)
            """,
            (
                f"episode_migrated_{row['id']}",
                row["campaign_id"],
                row["id"],
                "in_progress" if row["status"] == "active" else "ended",
                row["started_at"],
                row["ended_at"],
            ),
        )
