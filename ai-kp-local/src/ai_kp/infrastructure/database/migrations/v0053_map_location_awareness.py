"""Migration 53: per-player map location awareness."""

import sqlite3

VERSION = 53
NAME = "map_location_awareness"

DDL = """
CREATE TABLE IF NOT EXISTS map_location_awareness (
  id TEXT PRIMARY KEY,
  map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  player_profile_id TEXT NOT NULL
    REFERENCES player_profiles(id) ON DELETE CASCADE,
  location_id TEXT NOT NULL REFERENCES map_locations(id) ON DELETE CASCADE,
  state TEXT NOT NULL DEFAULT 'unknown'
    CHECK (state IN ('current', 'seen', 'unknown', 'destroyed')),
  version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, player_profile_id, location_id)
);
CREATE INDEX IF NOT EXISTS idx_map_location_awareness
  ON map_location_awareness(campaign_id, player_profile_id, state);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
