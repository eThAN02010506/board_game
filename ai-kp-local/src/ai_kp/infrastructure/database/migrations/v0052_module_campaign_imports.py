"""Migration 52: auditable import batches from approved module entities."""

import sqlite3

VERSION = 52
NAME = "module_campaign_imports"


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_module_imports (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE RESTRICT,
          status TEXT NOT NULL DEFAULT 'proposed'
            CHECK (status IN ('proposed', 'applied', 'revoked')),
          created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          applied_at TEXT,
          revoked_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_module_import_items (
          id TEXT PRIMARY KEY,
          import_id TEXT NOT NULL
            REFERENCES campaign_module_imports(id) ON DELETE CASCADE,
          item_kind TEXT NOT NULL
            CHECK (item_kind IN ('location', 'route', 'npc_profile')),
          module_entity_id TEXT REFERENCES module_entities(id) ON DELETE SET NULL,
          module_relation_id TEXT
            REFERENCES module_entity_relations(id) ON DELETE SET NULL,
          module_candidate_id TEXT
            REFERENCES module_knowledge_candidates(id) ON DELETE SET NULL,
          travel_location_id TEXT
            REFERENCES campaign_travel_locations(id) ON DELETE SET NULL,
          npc_id TEXT REFERENCES npcs(id) ON DELETE SET NULL,
          payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload_json)),
          UNIQUE(import_id, item_kind, module_entity_id, module_relation_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_campaign_module_imports_campaign
          ON campaign_module_imports(campaign_id, created_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_campaign_module_import_items_import
          ON campaign_module_import_items(import_id)
        """
    )
