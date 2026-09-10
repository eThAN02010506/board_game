import hashlib
import json
import sqlite3
import stat
from pathlib import Path

import pytest

from ai_kp.core.db import connect, init_db
from ai_kp.infrastructure.database.migrations import (
    apply_migrations,
    v0020_campaign_module_runs,
    v0021_module_run_version,
    v0022_knowledge_extraction_attempts,
    v0023_session_assignment_uniqueness,
    v0024_rule_source_ruleset_hash,
    v0045_check_visibility,
    v0047_check_random_evidence,
    v0062_scenario_contract_job_schema_repair,
    v0069_parallel_batch_run_authority,
    v0072_session_zero_safety,
    v0073_observers_and_table_messages,
    v0083_module_chunk_section_ancestry,
    v0084_persistent_store_identity,
    v0086_kernel_authority_basis,
)
from ai_kp.storage.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS


def test_v86_adds_idempotent_json_authority_basis_to_existing_receipts() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE scenario_command_batches (
              id TEXT PRIMARY KEY,
              preview_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO scenario_command_batches (id, preview_json) VALUES ('old', '{}')"
        )

        v0086_kernel_authority_basis.migrate(connection)
        v0086_kernel_authority_basis.migrate(connection)

        columns = [
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(scenario_command_batches)"
            ).fetchall()
        ]
        assert columns.count("authority_basis_json") == 1
        assert connection.execute(
            "SELECT authority_basis_json FROM scenario_command_batches WHERE id = 'old'"
        ).fetchone()[0] is None
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE scenario_command_batches SET authority_basis_json = '[]'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE scenario_command_batches SET authority_basis_json = 'not-json'"
            )
        connection.execute(
            "UPDATE scenario_command_batches SET authority_basis_json = '{}'"
        )
    finally:
        connection.close()

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


def test_v72_requires_session_zero_for_existing_campaigns() -> None:
    connection = connect(":memory:")
    try:
        connection.executescript(
            """
            CREATE TABLE campaigns (id TEXT PRIMARY KEY);
            INSERT INTO campaigns (id) VALUES ('legacy-campaign');
            """
        )

        v0072_session_zero_safety.migrate(connection)

        assert connection.execute(
            "SELECT session_zero_required FROM campaigns WHERE id = 'legacy-campaign'"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_v73_preserves_members_and_adds_observer_message_ledger() -> None:
    connection = connect(":memory:")
    try:
        connection.executescript(
            """
            CREATE TABLE session_members (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              campaign_id TEXT NOT NULL,
              role TEXT NOT NULL CHECK (role IN ('kp', 'player')),
              display_name TEXT NOT NULL,
              pc_id TEXT,
              token_hash TEXT NOT NULL UNIQUE,
              joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              revoked_at TEXT,
              player_profile_id TEXT
            );
            CREATE INDEX idx_session_members_legacy_session
              ON session_members(session_id);
            INSERT INTO session_members
              (id, session_id, campaign_id, role, display_name, token_hash)
            VALUES ('member-kp', 'session-1', 'campaign-1', 'kp', 'KP', 'hash');
            """
        )
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")

        v0073_observers_and_table_messages.migrate(connection)

        member_sql = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name = 'session_members'"
        ).fetchone()[0]
        assert "'observer'" in member_sql
        preserved_member = connection.execute(
            "SELECT role, display_name FROM session_members WHERE id = 'member-kp'"
        ).fetchone()
        assert tuple(preserved_member) == ("kp", "KP")
        assert connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name = 'idx_session_members_legacy_session'"
        ).fetchone() is not None
        assert _column_names(connection, "table_messages") >= {"sequence", "id"}
    finally:
        connection.close()


def test_parallel_batch_v68_upgrade_matches_the_fresh_schema() -> None:
    fresh = connect(":memory:")
    upgraded = connect(":memory:")
    try:
        init_db(fresh)
        init_db(upgraded)
        for table_name in (
            "parallel_action_batch_events",
            "parallel_action_batch_items",
            "parallel_action_batches",
        ):
            upgraded.execute(f"DROP TABLE {table_name}")
        upgraded.execute("DELETE FROM schema_migrations WHERE version >= 68")
        upgraded.commit()

        apply_migrations(upgraded)

        for table_name in (
            "parallel_action_batch_items",
            "parallel_action_batch_events",
        ):
            assert upgraded.execute(
                "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()[0] == fresh.execute(
                "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()[0]
        assert _column_names(upgraded, "parallel_action_batches") == _column_names(
            fresh, "parallel_action_batches"
        )
        unique_item_indexes = {
            tuple(
                column["name"]
                for column in upgraded.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                    (index["name"],),
                )
            )
            for index in upgraded.execute(
                "SELECT name FROM pragma_index_list('parallel_action_batch_items') "
                'WHERE "unique" = 1'
            )
        }
        unique_event_indexes = {
            tuple(
                column["name"]
                for column in upgraded.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                    (index["name"],),
                )
            )
            for index in upgraded.execute(
                "SELECT name FROM pragma_index_list('parallel_action_batch_events') "
                'WHERE "unique" = 1'
            )
        }
        assert ("action_id",) in unique_item_indexes
        assert ("proposal_id",) in unique_item_indexes
        assert ("adjudication_id",) in unique_item_indexes
        assert ("batch_id", "version") in unique_event_indexes
        assert upgraded.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == LATEST_SCHEMA_VERSION
        assert "model_configuration_version" in _column_names(
            upgraded, "scenario_contract_jobs"
        )
        assert _column_names(upgraded, "scenario_contract_jobs") == _column_names(
            fresh, "scenario_contract_jobs"
        )
        recovery_index_columns = tuple(
            row["name"]
            for row in upgraded.execute(
                "SELECT name FROM pragma_index_info("
                "'idx_parallel_action_batches_recovery') ORDER BY seqno"
            )
        )
        assert recovery_index_columns == (
            "campaign_id",
            "session_id",
            "status",
            "updated_at",
            "id",
        )
    finally:
        fresh.close()
        upgraded.close()


def test_parallel_batch_v69_supersedes_every_unsettled_legacy_batch_with_audit() -> None:
    connection = connect(":memory:")
    try:
        connection.executescript(
            """
            CREATE TABLE campaign_module_runs (id TEXT PRIMARY KEY, version INTEGER);
            CREATE TABLE parallel_action_batches (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              status TEXT NOT NULL,
              version INTEGER NOT NULL,
              attention_reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE parallel_action_batch_events (
              id TEXT PRIMARY KEY,
              batch_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              actor_member_id TEXT,
              payload_json TEXT NOT NULL
            );
            CREATE TABLE parallel_action_batch_items (
              batch_id TEXT NOT NULL,
              action_id TEXT NOT NULL,
              proposal_id TEXT NOT NULL,
              adjudication_id TEXT NOT NULL
            );
            CREATE TABLE player_actions (
              id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              resolved_at TEXT
            );
            CREATE TABLE turn_proposals (
              id TEXT PRIMARY KEY,
              status TEXT NOT NULL
            );
            CREATE TABLE player_action_adjudications (
              id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE skill_checks (
              id TEXT PRIMARY KEY,
              player_action_id TEXT,
              status TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO campaign_module_runs (id, version) VALUES ('run-1', 7);
            INSERT INTO parallel_action_batches (id, run_id, status, version)
            VALUES ('batch-1', 'run-1', 'awaiting_confirmation', 3);
            INSERT INTO parallel_action_batches
              (id, run_id, status, version, attention_reason)
            VALUES ('batch-2', 'run-1', 'needs_attention', 5, 'legacy pause');
            INSERT INTO player_actions (id, status) VALUES ('action-1', 'reviewed');
            INSERT INTO turn_proposals (id, status) VALUES ('proposal-1', 'draft');
            INSERT INTO player_action_adjudications (id, status)
            VALUES ('adjudication-1', 'pending');
            INSERT INTO skill_checks (id, player_action_id, status)
            VALUES ('check-1', 'action-1', 'requested');
            INSERT INTO parallel_action_batch_items
              (batch_id, action_id, proposal_id, adjudication_id)
            VALUES ('batch-1', 'action-1', 'proposal-1', 'adjudication-1');
            """
        )

        v0069_parallel_batch_run_authority.migrate(connection)

        batch = connection.execute(
            "SELECT * FROM parallel_action_batches WHERE id = 'batch-1'"
        ).fetchone()
        event = connection.execute(
            "SELECT * FROM parallel_action_batch_events WHERE batch_id = 'batch-1'"
        ).fetchone()
        assert batch["module_run_version"] == 7
        assert batch["status"] == "superseded"
        assert batch["version"] == 4
        assert "re-planned" in batch["attention_reason"]
        assert event["version"] == 4
        assert event["event_type"] == "supersede"
        assert json.loads(event["payload_json"])["migration"] == 69
        assert connection.execute(
            "SELECT status FROM player_actions WHERE id = 'action-1'"
        ).fetchone()[0] == "rejected"
        assert connection.execute(
            "SELECT status FROM turn_proposals WHERE id = 'proposal-1'"
        ).fetchone()[0] == "rejected"
        assert connection.execute(
            "SELECT status FROM player_action_adjudications "
            "WHERE id = 'adjudication-1'"
        ).fetchone()[0] == "superseded"
        assert connection.execute(
            "SELECT status FROM skill_checks WHERE id = 'check-1'"
        ).fetchone()[0] == "cancelled"
        legacy_attention = connection.execute(
            "SELECT * FROM parallel_action_batches WHERE id = 'batch-2'"
        ).fetchone()
        assert legacy_attention["status"] == "superseded"
        assert legacy_attention["version"] == 6
        assert legacy_attention["attention_reason"] == "legacy pause"

        # Direct migration invocation is idempotent and preserves its audit row.
        v0069_parallel_batch_run_authority.migrate(connection)
        assert connection.execute(
            "SELECT COUNT(*) FROM parallel_action_batch_events"
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_check_visibility_migration_backfills_legacy_hidden_rows() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(
            "CREATE TABLE skill_checks (id TEXT PRIMARY KEY, hidden INTEGER NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO skill_checks (id, hidden) VALUES (?, ?)",
            (("public", 0), ("hidden", 1)),
        )

        v0045_check_visibility.migrate(connection)

        rows = dict(
            connection.execute(
                "SELECT id, visibility FROM skill_checks ORDER BY id"
            ).fetchall()
        )
        assert rows == {"hidden": "blind", "public": "public"}
    finally:
        connection.close()


def test_random_evidence_migration_backfills_legacy_percentile_faces() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(
            """
            CREATE TABLE skill_checks (
              id TEXT PRIMARY KEY,
              raw_dice_json TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO skill_checks (id, raw_dice_json)
            VALUES (
              'legacy-check',
              '{"ones_digit":4,"tens_digits":[4,2],"candidates":[44,24]}'
            )
            """
        )

        v0047_check_random_evidence.migrate(connection)

        payload = json.loads(
            connection.execute(
                """
                SELECT random_evidence_json
                FROM skill_checks
                WHERE id = 'legacy-check'
                """
            ).fetchone()["random_evidence_json"]
        )
        assert payload["schema_version"] == "dice-roll.v1"
        assert payload["rolls"] == {
            "ones_digit": [4],
            "tens_digits": [4, 2],
        }
        assert len(payload["evidence_fingerprint"]) == 64
    finally:
        connection.close()


def test_scenario_contract_job_schema_repair_backfills_prototype_columns() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(
            "CREATE TABLE scenario_contract_jobs (id TEXT PRIMARY KEY)"
        )
        connection.execute(
            """
            CREATE TABLE scenario_contract_job_partitions (
              job_id TEXT NOT NULL,
              partition_index INTEGER NOT NULL,
              PRIMARY KEY (job_id, partition_index)
            )
            """
        )

        v0062_scenario_contract_job_schema_repair.migrate(connection)
        v0062_scenario_contract_job_schema_repair.migrate(connection)
        connection.execute(
            "INSERT INTO scenario_contract_jobs (id) VALUES ('job-1')"
        )
        connection.execute(
            """
            INSERT INTO scenario_contract_job_partitions (job_id, partition_index)
            VALUES ('job-1', 0)
            """
        )

        assert "retryable" in _column_names(connection, "scenario_contract_jobs")
        assert {
            "model_attempt_count",
            "validation_errors_json",
        } <= _column_names(connection, "scenario_contract_job_partitions")
        assert connection.execute(
            "SELECT retryable FROM scenario_contract_jobs WHERE id = 'job-1'"
        ).fetchone()["retryable"] == 1
        partition = connection.execute(
            """
            SELECT model_attempt_count, validation_errors_json
            FROM scenario_contract_job_partitions
            WHERE job_id = 'job-1' AND partition_index = 0
            """
        ).fetchone()
        assert partition["model_attempt_count"] == 0
        assert partition["validation_errors_json"] == "[]"
    finally:
        connection.close()


def _downgrade_rule_sources_to_v23(connection: sqlite3.Connection) -> None:
    connection.execute(
        "DELETE FROM schema_migrations WHERE version = ?",
        (v0024_rule_source_ruleset_hash.VERSION,),
    )
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    connection.execute(
        """
        CREATE TABLE rule_sources_v23 (
          id TEXT PRIMARY KEY,
          ruleset_id TEXT NOT NULL,
          title TEXT NOT NULL,
          source_filename TEXT NOT NULL,
          source_hash TEXT NOT NULL UNIQUE,
          page_count INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'extracted'
            CHECK (status IN ('extracting', 'extracted', 'indexing', 'ready', 'failed')),
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        INSERT INTO rule_sources_v23
          (id, ruleset_id, title, source_filename, source_hash, page_count,
           status, metadata_json, created_at, updated_at)
        SELECT
          id, ruleset_id, title, source_filename, source_hash, page_count,
          status, metadata_json, created_at, updated_at
        FROM rule_sources
        """
    )
    connection.execute("DROP TABLE rule_sources")
    connection.execute("ALTER TABLE rule_sources_v23 RENAME TO rule_sources")
    connection.execute(
        """
        CREATE INDEX idx_rule_sources_ruleset
          ON rule_sources(ruleset_id, status)
        """
    )
    connection.commit()
    connection.execute("PRAGMA foreign_keys = ON")
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


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
            "proposed_facts_json",
            "proposed_world_entity_states_json",
        }
        assert "client_action_id" in _column_names(connection, "player_actions")
        assert "visibility" in _column_names(connection, "skill_checks")
        assert "status" in _column_names(connection, "maps")
        assert "current_revision_id" in _column_names(connection, "maps")
        assert "element_id" in _column_names(connection, "map_locations")
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'map_revisions'"
        ).fetchone()
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'map_assets'"
        ).fetchone()
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'image_model_configuration'"
        ).fetchone()
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'memories_fts'"
        ).fetchone()
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'module_import_jobs'"
        ).fetchone()
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'module_assets'"
        ).fetchone()
        assert "source_locator" in _column_names(connection, "module_chunks")
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
    data_directory = tmp_path / "data"
    data_directory.mkdir(mode=0o755)
    data_directory.chmod(0o755)
    database_path = data_directory / "configured.sqlite3"
    file_connection = connect(database_path)
    performance_connection = connect(
        data_directory / "performance.sqlite3",
        synchronous="NORMAL",
    )
    memory_connection = connect(":memory:")
    try:
        assert file_connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert file_connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert file_connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert file_connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert performance_connection.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert stat.S_IMODE(data_directory.stat().st_mode) == 0o755
        assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
        file_connection.execute("CREATE TABLE permission_probe (id INTEGER PRIMARY KEY)")
        file_connection.commit()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{database_path}{suffix}")
            assert sidecar.exists()
            assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600

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
        performance_connection.close()
        memory_connection.close()


def test_connect_does_not_chmod_an_existing_custom_parent(tmp_path: Path) -> None:
    custom_parent = tmp_path / "shared-parent"
    custom_parent.mkdir(mode=0o755)
    custom_parent.chmod(0o755)
    database_path = custom_parent / "private.sqlite3"

    connection = connect(database_path)
    connection.close()

    assert stat.S_IMODE(custom_parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600


def test_connect_secures_the_owned_default_data_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    data_directory = tmp_path / "data"
    data_directory.mkdir(mode=0o755)
    data_directory.chmod(0o755)

    connection = connect(Path("data/ai_kp.sqlite3"))
    connection.close()

    assert stat.S_IMODE(data_directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(
        (data_directory / "ai_kp.sqlite3").stat().st_mode
    ) == 0o600


def test_module_run_version_migrates_a_database_already_at_v20() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        v0020_campaign_module_runs.migrate(connection)
        assert "version" not in _column_names(connection, "campaign_module_runs")

        v0021_module_run_version.migrate(connection)
        v0021_module_run_version.migrate(connection)

        assert "version" in _column_names(connection, "campaign_module_runs")
        version = connection.execute(
            """
            SELECT dflt_value
            FROM pragma_table_info('campaign_module_runs')
            WHERE name = 'version'
            """
        ).fetchone()
        assert version is not None
        assert version["dflt_value"] == "0"
    finally:
        connection.close()


def test_knowledge_attempts_migrate_a_database_already_at_v21() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(
            """
            CREATE TABLE rule_chunks (
              id TEXT PRIMARY KEY,
              extraction_status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE TABLE module_chunks (
              id TEXT PRIMARY KEY,
              knowledge_status TEXT NOT NULL DEFAULT 'pending'
            );
            INSERT INTO rule_chunks (id, extraction_status)
            VALUES ('rule_processing', 'processing');
            INSERT INTO module_chunks (id, knowledge_status)
            VALUES ('module_processing', 'processing');
            """
        )

        v0022_knowledge_extraction_attempts.migrate(connection)
        v0022_knowledge_extraction_attempts.migrate(connection)

        assert "attempt_count" in _column_names(connection, "rule_chunks")
        assert "attempt_count" in _column_names(connection, "module_chunks")
        assert connection.execute(
            "SELECT attempt_count FROM rule_chunks WHERE id = 'rule_processing'"
        ).fetchone()["attempt_count"] == 0
        assert connection.execute(
            "SELECT attempt_count FROM module_chunks WHERE id = 'module_processing'"
        ).fetchone()["attempt_count"] == 0
    finally:
        connection.close()


def test_module_chunk_section_ancestry_migration_is_idempotent() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("CREATE TABLE module_chunks (id TEXT PRIMARY KEY)")
        v0083_module_chunk_section_ancestry.migrate(connection)
        v0083_module_chunk_section_ancestry.migrate(connection)

        columns = _column_names(connection, "module_chunks")
        assert {"heading_level", "section_path_json"} <= columns
        connection.execute("INSERT INTO module_chunks (id) VALUES ('legacy')")
        row = connection.execute(
            "SELECT heading_level, section_path_json FROM module_chunks"
        ).fetchone()
        assert tuple(row) == (None, "[]")
    finally:
        connection.close()


def test_persistent_store_identity_migration_is_idempotent() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        v0084_persistent_store_identity.migrate(connection)
        first = connection.execute(
            "SELECT singleton, persistent_store_id FROM persistent_store_identity"
        ).fetchone()
        v0084_persistent_store_identity.migrate(connection)
        rows = connection.execute(
            "SELECT singleton, persistent_store_id FROM persistent_store_identity"
        ).fetchall()

        assert len(rows) == 1
        assert rows[0] == first
        assert first[0] == 1
        assert first[1].startswith("store_")
    finally:
        connection.close()


SESSION_ASSIGNMENT_SCHEMA = """
CREATE TABLE session_members (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  pc_id TEXT,
  revoked_at TEXT,
  player_profile_id TEXT
);
CREATE TABLE session_seats (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  status TEXT NOT NULL,
  assigned_pc_id TEXT,
  player_profile_id TEXT
);
"""


def test_session_assignment_uniqueness_migration_rejects_legacy_conflicts() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SESSION_ASSIGNMENT_SCHEMA)
        connection.executemany(
            """
            INSERT INTO session_members
              (id, session_id, role, pc_id, player_profile_id)
            VALUES (?, 'session_1', 'player', 'pc_1', 'profile_1')
            """,
            (("member_1",), ("member_2",)),
        )
        connection.executemany(
            """
            INSERT INTO session_seats
              (id, session_id, status, assigned_pc_id, player_profile_id)
            VALUES (?, 'session_1', 'claimed', 'pc_1', 'profile_1')
            """,
            (("seat_1",), ("seat_2",)),
        )

        with pytest.raises(RuntimeError, match="duplicate live rows") as error:
            v0023_session_assignment_uniqueness.migrate(connection)

        assert "active player members share one player profile" in str(error.value)
        assert "active session members share one PC" in str(error.value)
        assert "claimed seats share one player profile" in str(error.value)
        assert "active seats share one PC" in str(error.value)
        assert connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'index' AND name LIKE 'idx_session_%'
            """
        ).fetchall() == []
    finally:
        connection.close()


def test_session_assignment_partial_indexes_enforce_only_live_rows() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SESSION_ASSIGNMENT_SCHEMA)
        v0023_session_assignment_uniqueness.migrate(connection)
        v0023_session_assignment_uniqueness.migrate(connection)

        connection.execute(
            """
            INSERT INTO session_members
              (id, session_id, role, pc_id, player_profile_id)
            VALUES ('member_1', 'session_1', 'player', 'pc_1', 'profile_1')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO session_members
                  (id, session_id, role, pc_id, player_profile_id)
                VALUES ('member_2', 'session_1', 'player', 'pc_2', 'profile_1')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO session_members
                  (id, session_id, role, pc_id, player_profile_id)
                VALUES ('member_pc_duplicate', 'session_1', 'player', 'pc_1', 'profile_2')
                """
            )
        connection.execute(
            """
            INSERT INTO session_members
              (id, session_id, role, pc_id, revoked_at, player_profile_id)
            VALUES (
              'member_revoked', 'session_1', 'player', 'pc_1',
              CURRENT_TIMESTAMP, 'profile_1'
            )
            """
        )

        connection.execute(
            """
            INSERT INTO session_seats
              (id, session_id, status, assigned_pc_id, player_profile_id)
            VALUES ('seat_1', 'session_1', 'claimed', 'pc_1', 'profile_1')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO session_seats
                  (id, session_id, status, assigned_pc_id, player_profile_id)
                VALUES ('seat_2', 'session_1', 'claimed', 'pc_2', 'profile_1')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO session_seats
                  (id, session_id, status, assigned_pc_id, player_profile_id)
                VALUES ('seat_pc_duplicate', 'session_1', 'open', 'pc_1', 'profile_2')
                """
            )
        connection.execute(
            """
            INSERT INTO session_seats
              (id, session_id, status, assigned_pc_id, player_profile_id)
            VALUES ('seat_revoked', 'session_1', 'revoked', 'pc_1', 'profile_1')
            """
        )

        indexes = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'index' AND name LIKE 'idx_session_%'
                """
            )
        }
        assert indexes >= {
            "idx_session_members_active_profile",
            "idx_session_members_active_pc",
            "idx_session_seats_claimed_profile",
            "idx_session_seats_active_pc",
        }
    finally:
        connection.close()


def test_rule_source_ruleset_hash_upgrade_preserves_all_dependent_rows() -> None:
    connection = connect(":memory:")
    try:
        init_db(connection)
        _downgrade_rule_sources_to_v23(connection)
        source_hash = hashlib.sha256(b"legacy rulebook bytes").hexdigest()
        chunk_hash = hashlib.sha256(b"legacy source paragraph").hexdigest()
        object_hash = hashlib.sha256(b"legacy structured rule").hexdigest()
        evidence_hash = hashlib.sha256(b"legacy evidence").hexdigest()
        connection.execute(
            """
            INSERT INTO rule_sources
              (id, ruleset_id, title, source_filename, source_hash, page_count,
               status, metadata_json, created_at, updated_at)
            VALUES (
              'rulesource_legacy', 'coc7', 'Legacy rules', 'legacy.pdf', ?, 12,
              'ready', '{"edition":"legacy"}',
              '2024-01-01 00:00:00', '2024-02-01 00:00:00'
            )
            """,
            (source_hash,),
        )
        connection.execute(
            """
            INSERT INTO rule_ingestion_runs
              (id, source_id, status, stage, processed_count, accepted_count)
            VALUES (
              'ruleingest_legacy', 'rulesource_legacy', 'completed',
              'structured_rules', 1, 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO rule_chunks
              (id, source_id, page_start, page_end, order_index, audience,
               text, text_hash, extraction_status, attempt_count)
            VALUES (
              'rulechunk_legacy', 'rulesource_legacy', 3, 3, 0, 'kp',
              'legacy source paragraph', ?, 'completed', 1
            )
            """,
            (chunk_hash,),
        )
        connection.execute(
            """
            INSERT INTO rule_objects
              (id, source_id, rule_key, rule_type, title, status,
               object_json, object_hash, confidence)
            VALUES (
              'ruleobj_legacy', 'rulesource_legacy', 'legacy.rule',
              'resolution', 'Legacy rule', 'validated',
              '{"rule_key":"legacy.rule"}', ?, 1
            )
            """,
            (object_hash,),
        )
        connection.execute(
            """
            INSERT INTO rule_object_citations
              (rule_object_id, chunk_id, page, evidence_text, evidence_hash)
            VALUES (
              'ruleobj_legacy', 'rulechunk_legacy', 3,
              'legacy evidence', ?
            )
            """,
            (evidence_hash,),
        )
        connection.execute(
            """
            INSERT INTO rule_relations
              (id, source_id, source_rule_key, target_rule_key,
               relation_type, evidence_chunk_id)
            VALUES (
              'rulerel_legacy', 'rulesource_legacy', 'legacy.rule',
              'legacy.target', 'depends_on', 'rulechunk_legacy'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO rule_validation_issues
              (id, source_id, chunk_id, run_id, validation_layer, error_text)
            VALUES (
              'ruleissue_legacy', 'rulesource_legacy', 'rulechunk_legacy',
              'ruleingest_legacy', 'citation', 'legacy review note'
            )
            """
        )
        connection.commit()

        init_db(connection)

        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == LATEST_SCHEMA_VERSION
        source = connection.execute(
            "SELECT * FROM rule_sources WHERE id = 'rulesource_legacy'"
        ).fetchone()
        assert source is not None
        assert source["source_hash"] == source_hash
        assert source["metadata_json"] == '{"edition":"legacy"}'
        assert source["created_at"] == "2024-01-01 00:00:00"
        assert source["updated_at"] == "2024-02-01 00:00:00"

        expected_ids = {
            "rule_ingestion_runs": ("id", "ruleingest_legacy"),
            "rule_chunks": ("id", "rulechunk_legacy"),
            "rule_objects": ("id", "ruleobj_legacy"),
            "rule_object_citations": ("rule_object_id", "ruleobj_legacy"),
            "rule_relations": ("id", "rulerel_legacy"),
            "rule_validation_issues": ("id", "ruleissue_legacy"),
        }
        for table_name, (id_column, expected_id) in expected_ids.items():
            row = connection.execute(
                f"SELECT {id_column} FROM {table_name}"
            ).fetchone()
            assert row is not None
            assert row[0] == expected_id

        unique_columns = {
            tuple(
                str(column["name"])
                for column in connection.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                    (str(index["name"]),),
                )
            )
            for index in connection.execute(
                """
                SELECT name FROM pragma_index_list('rule_sources')
                WHERE "unique" = 1
                """
            )
        }
        assert ("source_hash",) not in unique_columns
        assert ("ruleset_id", "source_hash") in unique_columns

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO rule_sources
                  (id, ruleset_id, title, source_filename, source_hash, page_count)
                VALUES ('rulesource_duplicate', 'coc7', 'Duplicate', 'same.pdf', ?, 12)
                """,
                (source_hash,),
            )
        connection.execute(
            """
            INSERT INTO rule_sources
              (id, ruleset_id, title, source_filename, source_hash, page_count)
            VALUES ('rulesource_other_system', 'cyberpunk-red', 'Same bytes', 'same.pdf', ?, 12)
            """,
            (source_hash,),
        )

        connection.execute("SAVEPOINT cascade_probe")
        connection.execute(
            "DELETE FROM rule_sources WHERE id = 'rulesource_legacy'"
        )
        for table_name in expected_ids:
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0] == 0
        connection.execute("ROLLBACK TO SAVEPOINT cascade_probe")
        connection.execute("RELEASE SAVEPOINT cascade_probe")
        assert connection.execute(
            "SELECT COUNT(*) FROM rule_sources"
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_rule_source_ruleset_hash_upgrade_rolls_back_on_foreign_key_violation() -> None:
    connection = connect(":memory:")
    try:
        init_db(connection)
        _downgrade_rule_sources_to_v23(connection)
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO rule_chunks
              (id, source_id, page_start, page_end, order_index, audience,
               text, text_hash)
            VALUES (
              'rulechunk_orphan', 'rulesource_missing', 1, 1, 0, 'kp',
              'orphaned legacy text', ?
            )
            """,
            (hashlib.sha256(b"orphaned legacy text").hexdigest(),),
        )
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")

        with pytest.raises(RuntimeError, match="failed foreign key validation"):
            apply_migrations(connection)

        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 24"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM rule_chunks WHERE id = 'rulechunk_orphan'"
        ).fetchone() is not None
        assert connection.execute(
            """
            SELECT 1 FROM sqlite_schema
            WHERE type = 'table' AND name = 'rule_sources_v24'
            """
        ).fetchone() is None
        unique_columns = {
            tuple(
                str(column["name"])
                for column in connection.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                    (str(index["name"]),),
                )
            )
            for index in connection.execute(
                """
                SELECT name FROM pragma_index_list('rule_sources')
                WHERE "unique" = 1
                """
            )
        }
        assert ("source_hash",) in unique_columns
        assert ("ruleset_id", "source_hash") not in unique_columns
    finally:
        connection.close()
