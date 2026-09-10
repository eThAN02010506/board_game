"""Migration 77: authoritative campaign inventory and currency ledger."""

import sqlite3

VERSION = 77
NAME = "inventory_economy"


def migrate(connection: sqlite3.Connection) -> None:
    statements = """
        CREATE TABLE IF NOT EXISTS campaign_inventory_items (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          item_type TEXT NOT NULL,
          public_name TEXT NOT NULL,
          public_description TEXT NOT NULL DEFAULT '',
          publicly_listed INTEGER NOT NULL DEFAULT 0 CHECK (publicly_listed IN (0, 1)),
          quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 0),
          is_unique INTEGER NOT NULL DEFAULT 0 CHECK (is_unique IN (0, 1)),
          holder_kind TEXT NOT NULL
            CHECK (holder_kind IN ('investigator', 'party', 'npc', 'location', 'loot', 'none')),
          holder_id TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'available'
            CHECK (state IN ('available', 'consumed', 'broken', 'lost')),
          equipped_slot TEXT,
          weight_units INTEGER NOT NULL DEFAULT 0 CHECK (weight_units >= 0),
          unit_value_minor INTEGER NOT NULL DEFAULT 0 CHECK (unit_value_minor >= 0),
          currency_code TEXT NOT NULL DEFAULT '',
          use_effect_json TEXT NOT NULL DEFAULT '{}',
          hidden_properties_json TEXT NOT NULL DEFAULT '{}',
          known_member_ids_json TEXT NOT NULL DEFAULT '[]',
          source_refs_json TEXT NOT NULL DEFAULT '[]',
          version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK (is_unique = 0 OR quantity IN (0, 1)),
          CHECK (equipped_slot IS NULL OR holder_kind = 'investigator')
        );

        CREATE INDEX IF NOT EXISTS idx_inventory_items_holder
        ON campaign_inventory_items(campaign_id, holder_kind, holder_id, state);

        CREATE TABLE IF NOT EXISTS campaign_currency_accounts (
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          account_kind TEXT NOT NULL
            CHECK (account_kind IN ('investigator', 'party', 'npc', 'vendor')),
          account_id TEXT NOT NULL,
          currency_code TEXT NOT NULL,
          balance_minor INTEGER NOT NULL DEFAULT 0 CHECK (balance_minor >= 0),
          version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (campaign_id, account_kind, account_id, currency_code)
        );

        CREATE TABLE IF NOT EXISTS inventory_ledger_events (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          command_id TEXT NOT NULL,
          command_type TEXT NOT NULL,
          item_id TEXT REFERENCES campaign_inventory_items(id) ON DELETE SET NULL,
          actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
          reason TEXT NOT NULL DEFAULT '',
          before_json TEXT NOT NULL DEFAULT '{}',
          after_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(campaign_id, command_id)
        );

        CREATE INDEX IF NOT EXISTS idx_inventory_ledger_campaign
        ON inventory_ledger_events(campaign_id, created_at, id);

        CREATE TABLE IF NOT EXISTS inventory_transfer_offers (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          item_id TEXT NOT NULL REFERENCES campaign_inventory_items(id) ON DELETE CASCADE,
          quantity INTEGER NOT NULL CHECK (quantity > 0),
          from_investigator_id TEXT NOT NULL,
          to_investigator_id TEXT NOT NULL,
          offered_by_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE CASCADE,
          status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'accepted', 'declined', 'cancelled', 'stale')),
          item_version INTEGER NOT NULL CHECK (item_version > 0),
          version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          resolved_at TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_transfer_pending_item
        ON inventory_transfer_offers(item_id) WHERE status = 'pending';

        CREATE TABLE IF NOT EXISTS inventory_recipes (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          public_name TEXT NOT NULL,
          inputs_json TEXT NOT NULL DEFAULT '[]',
          output_template_json TEXT NOT NULL DEFAULT '{}',
          currency_cost_minor INTEGER NOT NULL DEFAULT 0 CHECK (currency_cost_minor >= 0),
          currency_code TEXT NOT NULL DEFAULT '',
          visibility TEXT NOT NULL DEFAULT 'table'
            CHECK (visibility IN ('table', 'kp')),
          active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
          source_refs_json TEXT NOT NULL DEFAULT '[]',
          version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    for statement in statements.split(";"):
        if statement.strip():
            connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
