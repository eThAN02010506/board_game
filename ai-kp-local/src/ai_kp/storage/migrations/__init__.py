"""Compatibility imports for the migrated database migration registry."""

from ai_kp.infrastructure.database.migrations import (
    LATEST_SCHEMA_VERSION,
    MIGRATIONS,
    Migration,
    apply_migrations,
)

__all__ = ["LATEST_SCHEMA_VERSION", "MIGRATIONS", "Migration", "apply_migrations"]
