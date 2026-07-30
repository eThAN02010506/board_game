"""Migration 25: source-linked scene direction and per-run clue state."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 25
NAME = "add_scene_director_runtime"


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(
        connection,
        "campaign_module_runs",
        "current_scene_title",
        "TEXT",
    )
    ensure_column(
        connection,
        "campaign_module_runs",
        "play_pace",
        "TEXT NOT NULL DEFAULT 'freeform' "
        "CHECK (play_pace IN ('freeform', 'structured', 'downtime'))",
    )
    ensure_column(
        connection,
        "campaign_module_runs",
        "current_location_entity_id",
        "TEXT REFERENCES module_entities(id) ON DELETE SET NULL",
    )
    ensure_column(
        connection,
        "campaign_module_runs",
        "scene_started_world_time",
        "TEXT",
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS module_run_scene_events (
          id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL
            REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
          from_scene_key TEXT,
          to_scene_key TEXT NOT NULL,
          from_scene_title TEXT,
          to_scene_title TEXT NOT NULL,
          from_play_pace TEXT,
          to_play_pace TEXT NOT NULL
            CHECK (to_play_pace IN ('freeform', 'structured', 'downtime')),
          from_location_entity_id TEXT
            REFERENCES module_entities(id) ON DELETE SET NULL,
          to_location_entity_id TEXT
            REFERENCES module_entities(id) ON DELETE SET NULL,
          world_time TEXT,
          note TEXT NOT NULL DEFAULT '',
          changed_by_member_id TEXT
            REFERENCES session_members(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_module_run_scene_events_run_created
          ON module_run_scene_events(run_id, created_at)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS module_run_entity_states (
          run_id TEXT NOT NULL
            REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
          entity_id TEXT NOT NULL
            REFERENCES module_entities(id) ON DELETE CASCADE,
          status TEXT NOT NULL DEFAULT 'hidden'
            CHECK (status IN ('hidden', 'available', 'discovered', 'resolved')),
          version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
          updated_by_member_id TEXT
            REFERENCES session_members(id) ON DELETE SET NULL,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (run_id, entity_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_module_run_entity_states_run_status
          ON module_run_entity_states(run_id, status, entity_id)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS module_run_entity_state_events (
          id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL
            REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
          entity_id TEXT NOT NULL
            REFERENCES module_entities(id) ON DELETE CASCADE,
          from_status TEXT NOT NULL
            CHECK (from_status IN ('hidden', 'available', 'discovered', 'resolved')),
          to_status TEXT NOT NULL
            CHECK (to_status IN ('hidden', 'available', 'discovered', 'resolved')),
          note TEXT NOT NULL DEFAULT '',
          changed_by_member_id TEXT
            REFERENCES session_members(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_module_run_entity_events_run_created
          ON module_run_entity_state_events(run_id, created_at)
        """
    )
