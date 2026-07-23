"""Compatibility import for the migrated schema migration."""
from ai_kp.infrastructure.database.migrations.v0010_session_seats import NAME, VERSION, migrate

__all__ = ["NAME", "VERSION", "migrate"]
