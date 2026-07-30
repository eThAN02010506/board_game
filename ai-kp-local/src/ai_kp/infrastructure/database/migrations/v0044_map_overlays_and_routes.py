"""Migration 44: revision-aware fog inheritance and replayable split routes."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 44
NAME = "add_map_overlay_inheritance_and_route_plans"


def migrate(connection: sqlite3.Connection) -> None:
    ensure_column(
        connection,
        "map_fog_regions",
        "inherited_from_fog_id",
        "TEXT REFERENCES map_fog_regions(id) ON DELETE SET NULL",
    )
    statements = (
        """
        CREATE TABLE IF NOT EXISTS map_route_plans (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
          revision_id TEXT NOT NULL REFERENCES map_revisions(id) ON DELETE RESTRICT,
          title TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'proposed'
            CHECK (status IN ('proposed', 'approved', 'executing', 'completed', 'cancelled')),
          visibility TEXT NOT NULL DEFAULT 'session'
            CHECK (visibility IN ('session', 'kp')),
          created_by_member_id TEXT NOT NULL
            REFERENCES session_members(id) ON DELETE RESTRICT,
          version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS map_route_plan_legs (
          id TEXT PRIMARY KEY,
          plan_id TEXT NOT NULL REFERENCES map_route_plans(id) ON DELETE CASCADE,
          token_id TEXT NOT NULL REFERENCES map_tokens(id) ON DELETE CASCADE,
          sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
          from_location_name TEXT NOT NULL,
          to_location_name TEXT NOT NULL,
          route_element_id TEXT,
          status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'completed', 'skipped')),
          completed_move_id TEXT REFERENCES map_token_moves(id) ON DELETE SET NULL,
          completed_at TEXT,
          UNIQUE(plan_id, token_id, sequence_no)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_map_route_plans_campaign
          ON map_route_plans(campaign_id, map_id, status, created_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_map_route_plan_legs_next
          ON map_route_plan_legs(plan_id, token_id, status, sequence_no)
        """,
    )
    for statement in statements:
        connection.execute(statement)


__all__ = ["NAME", "VERSION", "migrate"]
