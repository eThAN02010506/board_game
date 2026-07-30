"""Ordered, transactional SQLite schema migrations."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from ai_kp.infrastructure.database.migrations import (
    v0001_proposal_checks,
    v0002_player_action_idempotency,
    v0003_map_status,
    v0004_map_token_version,
    v0005_proposal_npc_updates,
    v0006_investigator_library,
    v0007_rulebook_knowledge,
    v0008_campaign_investigators,
    v0009_model_configuration,
    v0010_session_seats,
    v0011_skill_checks,
    v0012_map_spec_assets,
    v0013_map_revision_backfill,
    v0014_image_model_configuration,
    v0015_memory_fts,
    v0016_module_documents,
    v0017_module_knowledge,
    v0018_module_graph,
    v0019_module_document_structure,
    v0020_campaign_module_runs,
    v0021_module_run_version,
    v0022_knowledge_extraction_attempts,
    v0023_session_assignment_uniqueness,
    v0024_rule_source_ruleset_hash,
    v0025_scene_director_runtime,
)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    migrate: Callable[[sqlite3.Connection], None]
    requires_foreign_keys_off: bool = False


MIGRATIONS = (
    Migration(
        v0001_proposal_checks.VERSION,
        v0001_proposal_checks.NAME,
        v0001_proposal_checks.migrate,
    ),
    Migration(
        v0002_player_action_idempotency.VERSION,
        v0002_player_action_idempotency.NAME,
        v0002_player_action_idempotency.migrate,
    ),
    Migration(v0003_map_status.VERSION, v0003_map_status.NAME, v0003_map_status.migrate),
    Migration(
        v0004_map_token_version.VERSION,
        v0004_map_token_version.NAME,
        v0004_map_token_version.migrate,
    ),
    Migration(
        v0005_proposal_npc_updates.VERSION,
        v0005_proposal_npc_updates.NAME,
        v0005_proposal_npc_updates.migrate,
    ),
    Migration(
        v0006_investigator_library.VERSION,
        v0006_investigator_library.NAME,
        v0006_investigator_library.migrate,
    ),
    Migration(
        v0007_rulebook_knowledge.VERSION,
        v0007_rulebook_knowledge.NAME,
        v0007_rulebook_knowledge.migrate,
    ),
    Migration(
        v0008_campaign_investigators.VERSION,
        v0008_campaign_investigators.NAME,
        v0008_campaign_investigators.migrate,
    ),
    Migration(
        v0009_model_configuration.VERSION,
        v0009_model_configuration.NAME,
        v0009_model_configuration.migrate,
    ),
    Migration(
        v0010_session_seats.VERSION,
        v0010_session_seats.NAME,
        v0010_session_seats.migrate,
    ),
    Migration(
        v0011_skill_checks.VERSION,
        v0011_skill_checks.NAME,
        v0011_skill_checks.migrate,
    ),
    Migration(
        v0012_map_spec_assets.VERSION,
        v0012_map_spec_assets.NAME,
        v0012_map_spec_assets.migrate,
    ),
    Migration(
        v0013_map_revision_backfill.VERSION,
        v0013_map_revision_backfill.NAME,
        v0013_map_revision_backfill.migrate,
    ),
    Migration(
        v0014_image_model_configuration.VERSION,
        v0014_image_model_configuration.NAME,
        v0014_image_model_configuration.migrate,
    ),
    Migration(
        v0015_memory_fts.VERSION,
        v0015_memory_fts.NAME,
        v0015_memory_fts.migrate,
    ),
    Migration(
        v0016_module_documents.VERSION,
        v0016_module_documents.NAME,
        v0016_module_documents.migrate,
    ),
    Migration(
        v0017_module_knowledge.VERSION,
        v0017_module_knowledge.NAME,
        v0017_module_knowledge.migrate,
    ),
    Migration(
        v0018_module_graph.VERSION,
        v0018_module_graph.NAME,
        v0018_module_graph.migrate,
    ),
    Migration(
        v0019_module_document_structure.VERSION,
        v0019_module_document_structure.NAME,
        v0019_module_document_structure.migrate,
    ),
    Migration(
        v0020_campaign_module_runs.VERSION,
        v0020_campaign_module_runs.NAME,
        v0020_campaign_module_runs.migrate,
    ),
    Migration(
        v0021_module_run_version.VERSION,
        v0021_module_run_version.NAME,
        v0021_module_run_version.migrate,
    ),
    Migration(
        v0022_knowledge_extraction_attempts.VERSION,
        v0022_knowledge_extraction_attempts.NAME,
        v0022_knowledge_extraction_attempts.migrate,
    ),
    Migration(
        v0023_session_assignment_uniqueness.VERSION,
        v0023_session_assignment_uniqueness.NAME,
        v0023_session_assignment_uniqueness.migrate,
    ),
    Migration(
        v0024_rule_source_ruleset_hash.VERSION,
        v0024_rule_source_ruleset_hash.NAME,
        v0024_rule_source_ruleset_hash.migrate,
        requires_foreign_keys_off=True,
    ),
    Migration(
        v0025_scene_director_runtime.VERSION,
        v0025_scene_director_runtime.NAME,
        v0025_scene_director_runtime.migrate,
    ),
)

LATEST_SCHEMA_VERSION = MIGRATIONS[-1].version


def apply_migrations(connection: sqlite3.Connection) -> None:
    """Apply all unapplied migrations in order and record each atomically."""

    _validate_registry()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL UNIQUE,
          applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {
        int(row[0]): str(row[1])
        for row in connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    }
    future_versions = sorted(version for version in applied if version > LATEST_SCHEMA_VERSION)
    if future_versions:
        raise RuntimeError(
            "database schema is newer than this application: "
            f"found version {future_versions[-1]}, supports {LATEST_SCHEMA_VERSION}"
        )

    for migration in MIGRATIONS:
        recorded_name = applied.get(migration.version)
        if recorded_name is not None:
            if recorded_name != migration.name:
                raise RuntimeError(
                    f"schema migration {migration.version} name mismatch: "
                    f"database has {recorded_name!r}, application has {migration.name!r}"
                )
            continue

        savepoint = f"schema_migration_{migration.version:04d}"
        foreign_keys_were_enabled = False
        try:
            if migration.requires_foreign_keys_off:
                foreign_keys_were_enabled = bool(
                    connection.execute("PRAGMA foreign_keys").fetchone()[0]
                )
                if foreign_keys_were_enabled:
                    if connection.in_transaction:
                        raise RuntimeError(
                            f"schema migration {migration.version} requires foreign "
                            "keys to be disabled before starting its savepoint"
                        )
                    connection.execute("PRAGMA foreign_keys = OFF")
                    if connection.execute("PRAGMA foreign_keys").fetchone()[0]:
                        raise RuntimeError(
                            f"could not disable foreign keys for schema migration "
                            f"{migration.version}"
                        )

            connection.execute(f"SAVEPOINT {savepoint}")
            try:
                migration.migrate(connection)
                if migration.requires_foreign_keys_off:
                    violations = connection.execute(
                        "PRAGMA foreign_key_check"
                    ).fetchmany(5)
                    if violations:
                        details = [tuple(row) for row in violations]
                        raise RuntimeError(
                            f"schema migration {migration.version} failed foreign "
                            f"key validation: {details}"
                        )
                connection.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (migration.version, migration.name),
                )
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
        finally:
            if foreign_keys_were_enabled:
                connection.execute("PRAGMA foreign_keys = ON")
                if not connection.execute("PRAGMA foreign_keys").fetchone()[0]:
                    raise RuntimeError(
                        f"could not restore foreign keys after schema migration "
                        f"{migration.version}"
                    )


def _validate_registry() -> None:
    versions = [migration.version for migration in MIGRATIONS]
    names = [migration.name for migration in MIGRATIONS]
    if versions != list(range(1, len(MIGRATIONS) + 1)):
        raise RuntimeError("schema migration versions must be contiguous and start at 1")
    if len(names) != len(set(names)):
        raise RuntimeError("schema migration names must be unique")


__all__ = ["LATEST_SCHEMA_VERSION", "MIGRATIONS", "Migration", "apply_migrations"]
