"""Migration 89: versioned state ledger for campaign world entities."""

import sqlite3

VERSION = 89
NAME = "campaign_world_entity_states"


def migrate(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(campaign_world_entities)")
    }
    if "state_version" not in columns:
        connection.execute(
            "ALTER TABLE campaign_world_entities "
            "ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0"
        )
    for statement in _STATEMENTS:
        connection.execute(statement)


_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS campaign_world_entity_states (
      entity_id TEXT NOT NULL REFERENCES campaign_world_entities(id) ON DELETE CASCADE,
      dimension TEXT NOT NULL CHECK (
        length(dimension) BETWEEN 1 AND 64
        AND dimension NOT GLOB '*[^a-z0-9_]*'
      ),
      value_json TEXT NOT NULL CHECK (json_valid(value_json)),
      visibility TEXT NOT NULL CHECK (visibility IN ('table', 'kp', 'secret')),
      version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
      source_event_id TEXT NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (entity_id, dimension)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS campaign_world_entity_state_changes (
      id TEXT PRIMARY KEY,
      campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
      entity_id TEXT NOT NULL REFERENCES campaign_world_entities(id) ON DELETE CASCADE,
      dimension TEXT NOT NULL CHECK (length(dimension) BETWEEN 1 AND 64),
      from_value_json TEXT CHECK (from_value_json IS NULL OR json_valid(from_value_json)),
      to_value_json TEXT CHECK (to_value_json IS NULL OR json_valid(to_value_json)),
      visibility TEXT NOT NULL CHECK (visibility IN ('table', 'kp', 'secret')),
      note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 2000),
      source_kind TEXT NOT NULL CHECK (source_kind IN ('human_kp', 'ai_kp')),
      state_version INTEGER NOT NULL CHECK (state_version >= 1),
      idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 200),
      command_hash TEXT NOT NULL CHECK (length(command_hash) = 64),
      event_id TEXT NOT NULL UNIQUE REFERENCES events(id) ON DELETE RESTRICT,
      changed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(campaign_id, idempotency_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_world_entity_state_changes_entity
      ON campaign_world_entity_state_changes(entity_id, state_version)
    """,
)


__all__ = ["NAME", "VERSION", "migrate"]
