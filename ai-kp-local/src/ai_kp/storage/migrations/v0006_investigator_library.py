"""Compatibility import for the migrated schema migration."""
from ai_kp.infrastructure.database.migrations.v0006_investigator_library import (
    NAME,
    VERSION,
    migrate,
)

__all__ = ["NAME", "VERSION", "migrate"]
