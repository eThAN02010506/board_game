"""Add campaign travel graph and bounded NPC travel policy."""

import sqlite3

VERSION = 29
NAME = "add_campaign_travel_graph"

DDL = """
ALTER TABLE campaign_npc_reappearance_policies
  ADD COLUMN max_travel_minutes INTEGER NOT NULL DEFAULT 1440
  CHECK (max_travel_minutes BETWEEN 0 AND 525600);

CREATE TABLE IF NOT EXISTS campaign_travel_locations (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
  normalized_name TEXT NOT NULL CHECK (length(normalized_name) BETWEEN 1 AND 200),
  aliases_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(aliases_json)),
  source_kind TEXT NOT NULL DEFAULT 'manual'
    CHECK (source_kind IN ('manual', 'map', 'module')),
  source_ref TEXT,
  kp_notes TEXT NOT NULL DEFAULT '' CHECK (length(kp_notes) <= 2000),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, normalized_name)
);

CREATE TABLE IF NOT EXISTS campaign_travel_routes (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  from_location_id TEXT NOT NULL
    REFERENCES campaign_travel_locations(id) ON DELETE CASCADE,
  to_location_id TEXT NOT NULL
    REFERENCES campaign_travel_locations(id) ON DELETE CASCADE,
  travel_minutes INTEGER NOT NULL CHECK (travel_minutes BETWEEN 1 AND 525600),
  travel_mode TEXT NOT NULL DEFAULT 'other'
    CHECK (travel_mode IN ('walk', 'drive', 'rail', 'boat', 'flight', 'other')),
  bidirectional INTEGER NOT NULL DEFAULT 1 CHECK (bidirectional IN (0, 1)),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'blocked')),
  kp_notes TEXT NOT NULL DEFAULT '' CHECK (length(kp_notes) <= 2000),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (from_location_id != to_location_id),
  UNIQUE(campaign_id, from_location_id, to_location_id, travel_mode)
);

CREATE INDEX IF NOT EXISTS idx_campaign_travel_locations_campaign
  ON campaign_travel_locations(campaign_id, normalized_name);
CREATE INDEX IF NOT EXISTS idx_campaign_travel_routes_campaign
  ON campaign_travel_routes(campaign_id, status, from_location_id, to_location_id);
"""


def migrate(connection: sqlite3.Connection) -> None:
    statements = DDL.split(";")
    policy_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(campaign_npc_reappearance_policies)"
        ).fetchall()
    }
    for index, statement in enumerate(statements):
        if index == 0 and "max_travel_minutes" in policy_columns:
            continue
        if statement.strip():
            connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
