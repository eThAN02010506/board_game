"""Compatibility import for migration 37."""

from ai_kp.infrastructure.database.migrations.v0037_handouts import NAME, VERSION, migrate

__all__ = ["NAME", "VERSION", "migrate"]
