"""Add immutable KP-private NPC hidden-roll receipts."""

import sqlite3

VERSION = 30
NAME = "add_private_random_resolutions"

DDL = """
CREATE TABLE IF NOT EXISTS npc_hidden_appearance_resolutions (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  npc_id TEXT NOT NULL REFERENCES npcs(id) ON DELETE CASCADE,
  idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 200),
  command_hash TEXT NOT NULL CHECK (length(command_hash) = 64),
  trigger_text TEXT NOT NULL CHECK (length(trim(trigger_text)) BETWEEN 1 AND 500),
  appearance_chance INTEGER NOT NULL CHECK (appearance_chance BETWEEN 0 AND 100),
  appearance_roll INTEGER NOT NULL CHECK (appearance_roll BETWEEN 1 AND 100),
  appears INTEGER NOT NULL CHECK (appears IN (0, 1)),
  eligible_locations_json TEXT NOT NULL CHECK (json_valid(eligible_locations_json)),
  selected_location_id TEXT
    REFERENCES campaign_travel_locations(id) ON DELETE SET NULL,
  selected_location_name TEXT,
  location_roll INTEGER CHECK (location_roll IS NULL OR location_roll >= 1),
  created_by_member_id TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_npc_hidden_appearance_campaign_created
  ON npc_hidden_appearance_resolutions(campaign_id, created_at DESC, id DESC);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
