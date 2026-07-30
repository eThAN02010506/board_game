"""Add the idempotent encounter-to-world materialization ledger."""

import sqlite3

VERSION = 26
NAME = "add_world_expansion_materializations"

DDL = """
CREATE TABLE IF NOT EXISTS world_expansion_materializations (
  id TEXT PRIMARY KEY,
  proposal_id TEXT NOT NULL UNIQUE
    REFERENCES turn_proposals(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL
    REFERENCES campaigns(id) ON DELETE CASCADE,
  idempotency_key TEXT NOT NULL,
  command_hash TEXT NOT NULL CHECK (length(command_hash) = 64),
  encounter_event_id TEXT NOT NULL UNIQUE
    REFERENCES events(id) ON DELETE CASCADE,
  npc_id TEXT REFERENCES npcs(id) ON DELETE SET NULL,
  map_token_id TEXT REFERENCES map_tokens(id) ON DELETE SET NULL,
  created_by_member_id TEXT
    REFERENCES session_members(id) ON DELETE SET NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(campaign_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS world_expansion_materialization_facts (
  materialization_id TEXT NOT NULL
    REFERENCES world_expansion_materializations(id) ON DELETE CASCADE,
  fact_event_id TEXT NOT NULL UNIQUE
    REFERENCES events(id) ON DELETE CASCADE,
  order_index INTEGER NOT NULL,
  PRIMARY KEY (materialization_id, fact_event_id),
  UNIQUE(materialization_id, order_index)
);

CREATE INDEX IF NOT EXISTS idx_world_expansion_materializations_campaign
  ON world_expansion_materializations(campaign_id, created_at);
"""


def migrate(connection: sqlite3.Connection) -> None:
    # executescript() implicitly commits and would destroy the migration
    # runner's savepoint. These statements intentionally contain no triggers,
    # so executing each one preserves the outer atomic migration boundary.
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
