"""Migration 87: immutable module setting profiles and run selections."""

import sqlite3

VERSION = 87
NAME = "module_setting_profiles"


def migrate(connection: sqlite3.Connection) -> None:
    for statement in _STATEMENTS:
        connection.execute(statement)


_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS module_setting_profiles (
      id TEXT PRIMARY KEY,
      module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
      title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
      setting_pack_id TEXT NOT NULL CHECK (length(trim(setting_pack_id)) BETWEEN 1 AND 80),
      status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
      current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version > 0),
      created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
      updated_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(module_id, title)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS module_setting_profile_versions (
      profile_id TEXT NOT NULL REFERENCES module_setting_profiles(id) ON DELETE CASCADE,
      version INTEGER NOT NULL CHECK (version > 0),
      setting_pack_version TEXT NOT NULL
        CHECK (length(trim(setting_pack_version)) BETWEEN 1 AND 40),
      document_json TEXT NOT NULL
        CHECK (json_valid(document_json) AND json_type(document_json) = 'object'),
      content_hash TEXT NOT NULL CHECK (
        length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'
      ),
      created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (profile_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS module_run_setting_selections (
      run_id TEXT PRIMARY KEY REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
      profile_id TEXT NOT NULL REFERENCES module_setting_profiles(id) ON DELETE RESTRICT,
      profile_version INTEGER NOT NULL CHECK (profile_version > 0),
      settlement_id TEXT NOT NULL CHECK (length(trim(settlement_id)) BETWEEN 1 AND 160),
      version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
      updated_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (profile_id, profile_version)
        REFERENCES module_setting_profile_versions(profile_id, version) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS module_run_setting_selection_events (
      sequence INTEGER PRIMARY KEY AUTOINCREMENT,
      event_id TEXT NOT NULL UNIQUE,
      run_id TEXT NOT NULL REFERENCES campaign_module_runs(id) ON DELETE CASCADE,
      from_profile_id TEXT REFERENCES module_setting_profiles(id) ON DELETE SET NULL,
      from_profile_version INTEGER,
      from_settlement_id TEXT,
      to_profile_id TEXT NOT NULL REFERENCES module_setting_profiles(id) ON DELETE RESTRICT,
      to_profile_version INTEGER NOT NULL CHECK (to_profile_version > 0),
      to_settlement_id TEXT NOT NULL,
      changed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
      reason TEXT NOT NULL CHECK (length(trim(reason)) BETWEEN 1 AND 2000),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_module_setting_profiles_module
      ON module_setting_profiles(module_id, status, updated_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_module_run_setting_selection_events_run
      ON module_run_setting_selection_events(run_id, sequence DESC)
    """,
)


__all__ = ["NAME", "VERSION", "migrate"]
