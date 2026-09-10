"""Migration 88: ruleset-neutral campaign world entities and typed relations."""

import sqlite3

VERSION = 88
NAME = "campaign_world_entities"


def migrate(connection: sqlite3.Connection) -> None:
    for statement in _STATEMENTS:
        connection.execute(statement)


_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS campaign_world_entities (
      id TEXT PRIMARY KEY,
      campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
      entity_kind TEXT NOT NULL CHECK (entity_kind IN (
        'npc', 'organization', 'item', 'document', 'vehicle', 'event', 'clue_carrier'
      )),
      archetype_id TEXT,
      name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 240),
      description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 4000),
      visibility TEXT NOT NULL DEFAULT 'table'
        CHECK (visibility IN ('table', 'kp', 'secret')),
      origin_kind TEXT NOT NULL CHECK (origin_kind IN ('module_source', 'world_expansion')),
      origin_ref TEXT NOT NULL CHECK (length(trim(origin_ref)) BETWEEN 1 AND 240),
      npc_id TEXT REFERENCES npcs(id) ON DELETE SET NULL,
      created_from_event_id TEXT NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
      data_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(data_json) AND json_type(data_json) = 'object'),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(campaign_id, origin_kind, origin_ref)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS campaign_world_entity_relations (
      id TEXT PRIMARY KEY,
      campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
      source_entity_id TEXT NOT NULL
        REFERENCES campaign_world_entities(id) ON DELETE CASCADE,
      relation_slot_id TEXT NOT NULL CHECK (length(trim(relation_slot_id)) BETWEEN 1 AND 64),
      target_entity_id TEXT NOT NULL
        REFERENCES campaign_world_entities(id) ON DELETE CASCADE,
      created_from_event_id TEXT NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      CHECK (source_entity_id <> target_entity_id),
      UNIQUE(campaign_id, source_entity_id, relation_slot_id, target_entity_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS world_expansion_materialization_entities (
      materialization_id TEXT NOT NULL
        REFERENCES world_expansion_materializations(id) ON DELETE CASCADE,
      world_entity_id TEXT NOT NULL
        REFERENCES campaign_world_entities(id) ON DELETE RESTRICT,
      role TEXT NOT NULL CHECK (role IN ('source', 'generated', 'relation_target')),
      local_ref TEXT,
      order_index INTEGER NOT NULL,
      PRIMARY KEY (materialization_id, world_entity_id),
      UNIQUE(materialization_id, order_index)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_campaign_world_entities_campaign_kind
      ON campaign_world_entities(campaign_id, entity_kind, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_campaign_world_relations_source
      ON campaign_world_entity_relations(campaign_id, source_entity_id)
    """,
)


__all__ = ["NAME", "VERSION", "migrate"]
