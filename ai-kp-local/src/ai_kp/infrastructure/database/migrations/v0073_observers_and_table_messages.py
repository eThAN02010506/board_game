"""Add observer membership and the authoritative table-message ledger."""

import sqlite3

VERSION = 73
NAME = "observers_and_table_messages"


def migrate(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError("migration 73 requires foreign keys disabled")
    member_sql = str(
        connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = 'session_members'"
        ).fetchone()[0]
    )
    if "'observer'" not in member_sql:
        preserved = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT sql FROM sqlite_schema
                WHERE tbl_name = 'session_members'
                  AND type IN ('index', 'trigger') AND sql IS NOT NULL
                ORDER BY type, name
                """
            ).fetchall()
        ]
        connection.execute(
            """
            CREATE TABLE session_members_v73 (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          role TEXT NOT NULL CHECK (role IN ('kp', 'player', 'observer')),
          display_name TEXT NOT NULL,
          pc_id TEXT REFERENCES player_characters(id) ON DELETE SET NULL,
          token_hash TEXT NOT NULL UNIQUE,
          joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          private_history_from_sequence INTEGER NOT NULL DEFAULT 1 CHECK (
            private_history_from_sequence >= 1
          ),
          last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          revoked_at TEXT,
          player_profile_id TEXT REFERENCES player_profiles(id) ON DELETE SET NULL
        )
            """
        )
        connection.execute(
            """
            INSERT INTO session_members_v73
          (id, session_id, campaign_id, role, display_name, pc_id, token_hash,
           joined_at, private_history_from_sequence, last_seen_at, revoked_at,
           player_profile_id)
        SELECT id, session_id, campaign_id, role, display_name, pc_id, token_hash,
               joined_at, 1, last_seen_at, revoked_at, player_profile_id
        FROM session_members
            """
        )
        connection.execute("DROP TABLE session_members")
        connection.execute("ALTER TABLE session_members_v73 RENAME TO session_members")
        for statement in preserved:
            connection.execute(statement)
    statements = (
        """
        CREATE TABLE IF NOT EXISTS table_messages (
          sequence INTEGER PRIMARY KEY AUTOINCREMENT,
          id TEXT NOT NULL UNIQUE,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
          sender_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
          audience TEXT NOT NULL CHECK (
            audience IN ('table', 'party', 'announcement', 'direct')
          ),
          recipient_member_id TEXT REFERENCES session_members(id) ON DELETE RESTRICT,
          content TEXT NOT NULL CHECK (length(trim(content)) BETWEEN 1 AND 4000),
          client_message_id TEXT NOT NULL CHECK (
            length(client_message_id) BETWEEN 8 AND 200
          ),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK ((audience = 'direct') = (recipient_member_id IS NOT NULL)),
          UNIQUE(sender_member_id, client_message_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_table_messages_session_time
          ON table_messages(session_id, sequence DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_table_messages_recipient
          ON table_messages(recipient_member_id, sequence DESC)
          WHERE recipient_member_id IS NOT NULL
        """,
    )
    for statement in statements:
        connection.execute(statement)
