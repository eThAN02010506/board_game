"""Migration 50: player-confirmed AI action adjudications."""

import sqlite3

VERSION = 50
NAME = "add_action_adjudications"


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_action_adjudications (
          id TEXT PRIMARY KEY,
          action_id TEXT NOT NULL UNIQUE REFERENCES player_actions(id) ON DELETE CASCADE,
          proposal_id TEXT NOT NULL REFERENCES turn_proposals(id) ON DELETE CASCADE,
          mode TEXT NOT NULL CHECK (mode IN (
            'direct_resolution', 'skill_check', 'roleplay_or_clarification'
          )),
          status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
            'pending', 'confirmed', 'superseded'
          )),
          version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
          reason TEXT NOT NULL,
          prompt TEXT NOT NULL DEFAULT '',
          skill_options_json TEXT NOT NULL DEFAULT '[]',
          selected_skill TEXT,
          source_model TEXT NOT NULL DEFAULT 'unknown',
          source_error TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          confirmed_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_action_adjudication_events (
          id TEXT PRIMARY KEY,
          adjudication_id TEXT NOT NULL REFERENCES player_action_adjudications(id)
            ON DELETE CASCADE,
          version INTEGER NOT NULL,
          event_type TEXT NOT NULL CHECK (event_type IN (
            'created', 'skill_changed', 'confirmed', 'superseded'
          )),
          actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_action_adjudication_events
        ON player_action_adjudication_events(adjudication_id, created_at, id)
        """
    )
