"""Add versioned Session 0 agreements and explanation-free safety events."""

import sqlite3

VERSION = 72
NAME = "session_zero_safety"


def migrate(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(campaigns)").fetchall()
    }
    if "session_zero_required" not in columns:
        connection.execute(
            "ALTER TABLE campaigns ADD COLUMN session_zero_required INTEGER NOT NULL "
            "DEFAULT 0 CHECK (session_zero_required IN (0, 1))"
        )
    # Existing product campaigns must enter the same consent boundary as new
    # campaigns. They remain recoverable: the active KP creates revision 1
    # through the normal Session 0 UI before further player actions.
    connection.execute("UPDATE campaigns SET session_zero_required = 1")
    sql = """
        CREATE TABLE IF NOT EXISTS campaign_setup_revisions (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          version INTEGER NOT NULL CHECK (version > 0),
          status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'superseded')),
          config_json TEXT NOT NULL CHECK (json_valid(config_json)),
          created_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          activated_at TEXT,
          UNIQUE(campaign_id, version)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_setup_pending
          ON campaign_setup_revisions(campaign_id) WHERE status = 'pending';
        CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_setup_active
          ON campaign_setup_revisions(campaign_id) WHERE status = 'active';

        CREATE TABLE IF NOT EXISTS session_zero_preferences (
          revision_id TEXT NOT NULL
            REFERENCES campaign_setup_revisions(id) ON DELETE CASCADE,
          member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE CASCADE,
          public_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(public_json)),
          private_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(private_json)),
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (revision_id, member_id)
        );

        CREATE TABLE IF NOT EXISTS session_zero_confirmations (
          revision_id TEXT NOT NULL
            REFERENCES campaign_setup_revisions(id) ON DELETE CASCADE,
          member_id TEXT NOT NULL REFERENCES session_members(id) ON DELETE CASCADE,
          confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (revision_id, member_id)
        );

        CREATE TABLE IF NOT EXISTS session_safety_events (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          session_id TEXT NOT NULL REFERENCES campaign_sessions(id) ON DELETE CASCADE,
          actor_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
          response_kind TEXT NOT NULL CHECK (
            response_kind IN ('pause', 'fade', 'change', 'rewind')
          ),
          status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'resolved')),
          run_id TEXT REFERENCES campaign_module_runs(id) ON DELETE SET NULL,
          prior_control_mode TEXT CHECK (
            prior_control_mode IS NULL OR
            prior_control_mode IN ('ai_assist', 'safety_paused', 'human_kp')
          ),
          public_message TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          resolved_at TEXT,
          resolved_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
          resolution_kind TEXT CHECK (
            resolution_kind IS NULL OR
            resolution_kind IN ('fade', 'change', 'rewind', 'resume')
          )
        );
        CREATE INDEX IF NOT EXISTS idx_session_safety_active
          ON session_safety_events(session_id, status, created_at, id);
        """
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(statement)
