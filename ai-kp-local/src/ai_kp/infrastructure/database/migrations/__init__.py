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
)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    migrate: Callable[[sqlite3.Connection], None]


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
        connection.execute(f"SAVEPOINT {savepoint}")
        try:
            migration.migrate(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise


def _validate_registry() -> None:
    versions = [migration.version for migration in MIGRATIONS]
    names = [migration.name for migration in MIGRATIONS]
    if versions != list(range(1, len(MIGRATIONS) + 1)):
        raise RuntimeError("schema migration versions must be contiguous and start at 1")
    if len(names) != len(set(names)):
        raise RuntimeError("schema migration names must be unique")


__all__ = ["LATEST_SCHEMA_VERSION", "MIGRATIONS", "Migration", "apply_migrations"]
