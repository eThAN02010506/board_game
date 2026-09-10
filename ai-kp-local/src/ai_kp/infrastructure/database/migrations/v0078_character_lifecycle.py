"""Migration 78: authoritative character and member lifecycle."""

import sqlite3

VERSION = 78
NAME = "character_lifecycle"


def migrate(connection: sqlite3.Connection) -> None:
    statements = """
        CREATE TABLE IF NOT EXISTS campaign_investigator_lifecycle (
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          investigator_id TEXT NOT NULL REFERENCES investigators(id) ON DELETE CASCADE,
          state TEXT NOT NULL DEFAULT 'active' CHECK (state IN (
            'active', 'incapacitated', 'dead', 'retired', 'departed', 'npc_controlled'
          )),
          version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
          last_source_event_id TEXT,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (campaign_id, investigator_id)
        );

        CREATE TABLE IF NOT EXISTS campaign_member_presence (
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
          member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE CASCADE,
          state TEXT NOT NULL DEFAULT 'active' CHECK (state IN (
            'active', 'observing', 'temporarily_absent', 'departed', 'npc_controlled'
          )),
          investigator_id TEXT REFERENCES investigators(id) ON DELETE SET NULL,
          version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (campaign_id, member_id)
        );

        CREATE TABLE IF NOT EXISTS character_lifecycle_requests (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
          member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
          investigator_id TEXT REFERENCES investigators(id) ON DELETE SET NULL,
          replacement_investigator_id TEXT REFERENCES investigators(id) ON DELETE SET NULL,
          action TEXT NOT NULL CHECK (action IN (
            'observe', 'replace', 'retire', 'temporary_leave', 'npc_control',
            'return', 'resurrect'
          )),
          status TEXT NOT NULL DEFAULT 'awaiting_player' CHECK (status IN (
            'awaiting_player', 'applied', 'rejected', 'cancelled'
          )),
          reason TEXT NOT NULL,
          base_lifecycle_version INTEGER,
          proposed_by_member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE RESTRICT,
          decided_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
          version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          decided_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_lifecycle_request_pending
          ON character_lifecycle_requests(campaign_id, member_id)
          WHERE status = 'awaiting_player';

        CREATE TABLE IF NOT EXISTS character_lifecycle_events (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          session_id TEXT REFERENCES campaign_sessions(id) ON DELETE SET NULL,
          investigator_id TEXT REFERENCES investigators(id) ON DELETE SET NULL,
          member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
          command_id TEXT NOT NULL,
          action TEXT NOT NULL,
          from_state TEXT,
          to_state TEXT NOT NULL,
          reason TEXT NOT NULL,
          source_event_id TEXT,
          actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
          public_summary TEXT NOT NULL,
          details_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(campaign_id, command_id)
        );
        CREATE INDEX IF NOT EXISTS idx_lifecycle_events_campaign
          ON character_lifecycle_events(campaign_id, created_at, id);
        """
    for statement in statements.split(";"):
        if statement.strip():
            connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
