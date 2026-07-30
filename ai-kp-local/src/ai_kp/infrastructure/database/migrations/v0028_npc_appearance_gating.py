"""Add deterministic NPC availability policy and appearance provenance."""

import sqlite3

VERSION = 28
NAME = "add_npc_appearance_gating"

DDL = """
CREATE TABLE IF NOT EXISTS npc_availability_profiles (
  npc_id TEXT PRIMARY KEY REFERENCES npcs(id) ON DELETE CASCADE,
  lifecycle_state TEXT NOT NULL DEFAULT 'unknown'
    CHECK (lifecycle_state IN ('unknown', 'active', 'missing', 'unavailable')),
  born_year INTEGER CHECK (born_year IS NULL OR born_year BETWEEN 1 AND 9999),
  died_year INTEGER CHECK (died_year IS NULL OR died_year BETWEEN 1 AND 9999),
  active_from_year INTEGER
    CHECK (active_from_year IS NULL OR active_from_year BETWEEN 1 AND 9999),
  active_until_year INTEGER
    CHECK (active_until_year IS NULL OR active_until_year BETWEEN 1 AND 9999),
  location_tags_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(location_tags_json)),
  profession_tags_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(profession_tags_json)),
  kp_notes TEXT NOT NULL DEFAULT '' CHECK (length(kp_notes) <= 2000),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (died_year IS NULL OR born_year IS NULL OR died_year >= born_year),
  CHECK (
    active_until_year IS NULL OR active_from_year IS NULL
    OR active_until_year >= active_from_year
  )
);

CREATE TABLE IF NOT EXISTS campaign_npc_reappearance_policies (
  campaign_id TEXT PRIMARY KEY REFERENCES campaigns(id) ON DELETE CASCADE,
  max_returning_npcs INTEGER NOT NULL DEFAULT 1
    CHECK (max_returning_npcs BETWEEN 0 AND 50),
  require_location_match INTEGER NOT NULL DEFAULT 0
    CHECK (require_location_match IN (0, 1)),
  require_profession_match INTEGER NOT NULL DEFAULT 0
    CHECK (require_profession_match IN (0, 1)),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS npc_reappearance_appearances (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  npc_id TEXT NOT NULL REFERENCES npcs(id) ON DELETE CASCADE,
  materialization_id TEXT NOT NULL UNIQUE
    REFERENCES world_expansion_materializations(id) ON DELETE CASCADE,
  appeared_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, npc_id)
);

CREATE INDEX IF NOT EXISTS idx_npc_reappearance_appearances_campaign
  ON npc_reappearance_appearances(campaign_id, created_at);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
