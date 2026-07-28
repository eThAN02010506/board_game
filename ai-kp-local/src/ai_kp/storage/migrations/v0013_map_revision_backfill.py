"""Compatibility import for the migrated schema migration."""

from ai_kp.infrastructure.database.migrations.v0013_map_revision_backfill import (
    NAME,
    VERSION,
    migrate,
)

__all__ = ["NAME", "VERSION", "migrate"]
