"""Compatibility import for the migrated schema migration."""
from ai_kp.infrastructure.database.migrations.v0003_map_status import NAME, VERSION, migrate

__all__ = ["NAME", "VERSION", "migrate"]
