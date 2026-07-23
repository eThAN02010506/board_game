"""Compatibility import for the migrated schema migration."""
from ai_kp.infrastructure.database.migrations.v0007_rulebook_knowledge import NAME, VERSION, migrate

__all__ = ["NAME", "VERSION", "migrate"]
