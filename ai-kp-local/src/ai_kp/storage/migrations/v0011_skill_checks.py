"""Compatibility import for the migrated schema migration."""
from ai_kp.infrastructure.database.migrations.v0011_skill_checks import NAME, VERSION, migrate

__all__ = ["NAME", "VERSION", "migrate"]
