"""Migration 10: session seats."""

import sqlite3

VERSION = 10
NAME = "add_per_seat_invitations"


DDL = """
CREATE TABLE IF NOT EXISTS session_seats (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'claimed', 'revoked')),
  assigned_pc_id TEXT REFERENCES player_characters(id) ON DELETE SET NULL,
  player_profile_id TEXT REFERENCES player_profiles(id) ON DELETE SET NULL,
  claimed_member_id TEXT UNIQUE REFERENCES session_members(id) ON DELETE SET NULL,
  created_by_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
  claimed_at TEXT,
  revoked_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS seat_invitations (
  id TEXT PRIMARY KEY,
  seat_id TEXT NOT NULL REFERENCES session_seats(id) ON DELETE CASCADE,
  invitation_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'consumed', 'revoked')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  consumed_at TEXT,
  revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_session_seats_session
  ON session_seats(session_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_session_seats_profile
  ON session_seats(player_profile_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_seat_invitations_seat
  ON seat_invitations(seat_id, status, created_at);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
