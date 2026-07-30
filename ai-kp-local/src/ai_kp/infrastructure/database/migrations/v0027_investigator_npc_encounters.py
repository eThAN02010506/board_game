"""Add source-linked stable investigator/NPC encounter history."""

import sqlite3

VERSION = 27
NAME = "add_investigator_npc_encounters"

DDL = """
CREATE TABLE IF NOT EXISTS investigator_npc_encounters (
  id TEXT PRIMARY KEY,
  investigator_id TEXT NOT NULL
    REFERENCES investigators(id) ON DELETE CASCADE,
  npc_id TEXT NOT NULL
    REFERENCES npcs(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL
    REFERENCES campaigns(id) ON DELETE CASCADE,
  source_event_id TEXT NOT NULL
    REFERENCES events(id) ON DELETE CASCADE,
  materialization_id TEXT
    REFERENCES world_expansion_materializations(id) ON DELETE SET NULL,
  interaction_summary TEXT NOT NULL
    CHECK (length(trim(interaction_summary)) BETWEEN 1 AND 1000),
  happened_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(investigator_id, npc_id, source_event_id)
);

CREATE INDEX IF NOT EXISTS idx_investigator_npc_encounters_investigator
  ON investigator_npc_encounters(investigator_id, npc_id, created_at);

CREATE INDEX IF NOT EXISTS idx_investigator_npc_encounters_npc
  ON investigator_npc_encounters(npc_id, campaign_id, created_at);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
