"""Compatibility import for the migrated schema migration."""

from ai_kp.infrastructure.database.migrations.v0012_map_spec_assets import (
    NAME,
    VERSION,
    migrate,
)

__all__ = ["NAME", "VERSION", "migrate"]
