import sqlite3
from pathlib import Path

import pytest

from ai_kp.core.db import connect, init_db
from ai_kp.storage.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS


LEGACY_SCHEMA = """
CREATE TABLE maps (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  title TEXT NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  style TEXT NOT NULL DEFAULT 'investigation',
  width INTEGER NOT NULL DEFAULT 960,
  height INTEGER NOT NULL DEFAULT 640,
  svg_text TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'ai',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE map_tokens (
  id TEXT PRIMARY KEY,
  map_id TEXT NOT NULL,
  label TEXT NOT NULL,
  actor_type TEXT NOT NULL DEFAULT 'pc',
  actor_id TEXT,
  location_id TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'table',
  color TEXT NOT NULL DEFAULT '#b93f2d',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE turn_proposals (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  pc_id TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  player_action TEXT NOT NULL,
  public_narration TEXT NOT NULL,
  kp_notes TEXT NOT NULL DEFAULT '',
  proposed_events_json TEXT NOT NULL DEFAULT '[]',
  proposed_memories_json TEXT NOT NULL DEFAULT '[]',
  proposed_map_moves_json TEXT NOT NULL DEFAULT '[]',
  source_model TEXT NOT NULL DEFAULT 'unknown',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  applied_at TEXT
);
CREATE TABLE player_actions (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  pc_id TEXT,
  action_text TEXT NOT NULL,
  location TEXT,
  map_id TEXT,
  status TEXT NOT NULL DEFAULT 'submitted',
  proposal_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT
);
"""


def _column_names(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table_name})")}


def test_init_db_migrates_legacy_schema_once_and_is_idempotent(tmp_path: Path) -> None:
    connection = connect(tmp_path / "legacy.sqlite3")
    try:
        connection.executescript(LEGACY_SCHEMA)
        connection.execute(
            """
            INSERT INTO maps
              (id, campaign_id, title, prompt, style, width, height, svg_text, created_by)
            VALUES (
              'map_legacy', 'camp_legacy', '旧地图', '旧入口与旧走廊',
              'investigation', 960, 640, '<svg></svg>', 'legacy'
            )
            """
        )

        init_db(connection)
        first_records = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        init_db(connection)
        second_records = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()

        assert _column_names(connection, "turn_proposals") >= {
            "proposed_checks_json",
            "proposed_npc_updates_json",
        }
        assert "client_action_id" in _column_names(connection, "player_actions")
        assert "status" in _column_names(connection, "maps")
        assert "current_revision_id" in _column_names(connection, "maps")
        assert "element_id" in _column_names(connection, "map_locations")
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'map_revisions'"
        ).fetchone()
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'map_assets'"
        ).fetchone()
        legacy_map = connection.execute(
            "SELECT current_revision_id FROM maps LIMIT 1"
        ).fetchone()
        assert legacy_map is not None
        assert legacy_map["current_revision_id"]
        assert connection.execute(
            "SELECT spec_version FROM map_revisions WHERE id = ?",
            (legacy_map["current_revision_id"],),
        ).fetchone()["spec_version"] == "map-spec.v1"
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'trg_maps_current_revision_guard'"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE maps SET current_revision_id = 'maprev_wrong' "
                "WHERE id = (SELECT id FROM maps LIMIT 1)"
            )
        assert "version" in _column_names(connection, "map_tokens")
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'idx_player_actions_idempotency'"
        ).fetchone()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()

    expected = [(migration.version, migration.name) for migration in MIGRATIONS]
    assert [tuple(row) for row in first_records] == expected
    assert [tuple(row) for row in second_records] == expected


def test_legacy_map_that_fails_current_validation_does_not_block_upgrade(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "small-legacy-map.sqlite3")
    try:
        connection.executescript(LEGACY_SCHEMA)
        connection.execute(
            """
            INSERT INTO maps
              (id, campaign_id, title, prompt, style, width, height, svg_text, created_by)
            VALUES (
              'map_small', 'camp_legacy', '旧版小地图', '旧系统允许的小画布',
              'investigation', 100, 100, '<svg></svg>', 'legacy'
            )
            """
        )

        init_db(connection)

        row = connection.execute(
            """
            SELECT r.validation_json
            FROM maps m
            JOIN map_revisions r ON r.id = m.current_revision_id
            WHERE m.id = 'map_small'
            """
        ).fetchone()
        assert row is not None
        assert '"valid": false' in row["validation_json"]
        assert '"canvas_size"' in row["validation_json"]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_connect_configures_file_and_memory_databases(tmp_path: Path) -> None:
    file_connection = connect(tmp_path / "configured.sqlite3")
    memory_connection = connect(":memory:")
    try:
        assert file_connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert file_connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert file_connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

        assert memory_connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert memory_connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert memory_connection.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
        init_db(memory_connection)
        init_db(memory_connection)
        version = memory_connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        assert version == LATEST_SCHEMA_VERSION
        assert memory_connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        file_connection.close()
        memory_connection.close()
