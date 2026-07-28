"""Compatibility import for the migrated schema migration."""
from ai_kp.infrastructure.database.migrations.v0008_campaign_investigators import (
    NAME,
    VERSION,
    migrate,
)

__all__ = ["NAME", "VERSION", "migrate"]
