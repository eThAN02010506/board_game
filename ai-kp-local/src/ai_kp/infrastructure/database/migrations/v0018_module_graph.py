"""Migration 18: provenance-bound module entities and directed relations."""

import sqlite3

VERSION = 18
NAME = "add_module_entity_graph"


def migrate(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS module_entities (
          id TEXT PRIMARY KEY,
          module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
          entity_type TEXT NOT NULL CHECK (
            entity_type IN (
              'npc', 'location', 'clue', 'organization', 'item', 'event', 'anchor'
            )
          ),
          name TEXT NOT NULL,
          normalized_name TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          visibility TEXT NOT NULL DEFAULT 'kp'
            CHECK (visibility IN ('player', 'table', 'kp', 'secret')),
          spoiler_tag TEXT,
          source_candidate_id TEXT NOT NULL
            REFERENCES module_knowledge_candidates(id) ON DELETE RESTRICT,
          created_by_member_id TEXT
            REFERENCES session_members(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(module_id, entity_type, normalized_name)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS module_entity_relations (
          id TEXT PRIMARY KEY,
          module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
          source_entity_id TEXT NOT NULL
            REFERENCES module_entities(id) ON DELETE CASCADE,
          predicate TEXT NOT NULL CHECK (
            predicate IN (
              'contains', 'located_at', 'knows', 'owns', 'member_of',
              'reveals', 'leads_to', 'provides_access_to', 'blocks',
              'contradicts', 'same_as', 'involves'
            )
          ),
          target_entity_id TEXT NOT NULL
            REFERENCES module_entities(id) ON DELETE CASCADE,
          source_candidate_id TEXT NOT NULL
            REFERENCES module_knowledge_candidates(id) ON DELETE RESTRICT,
          confidence REAL NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
          visibility TEXT NOT NULL DEFAULT 'kp'
            CHECK (visibility IN ('player', 'table', 'kp', 'secret')),
          spoiler_tag TEXT,
          note TEXT NOT NULL DEFAULT '',
          created_by_member_id TEXT
            REFERENCES session_members(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (source_entity_id <> target_entity_id),
          UNIQUE(module_id, source_entity_id, predicate, target_entity_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_module_entities_module_type
          ON module_entities(module_id, entity_type, name)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_module_entity_relations_source
          ON module_entity_relations(module_id, source_entity_id, predicate)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_module_entity_relations_target
          ON module_entity_relations(module_id, target_entity_id, predicate)
        """,
    )
    for statement in statements:
        connection.execute(statement)
