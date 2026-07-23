"""Compatibility import for the migrated schema migration."""
from ai_kp.infrastructure.database.migrations.v0004_map_token_version import NAME, VERSION, migrate

__all__ = ["NAME", "VERSION", "migrate"]
