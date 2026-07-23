"""Compatibility import for the migrated schema migration."""
from ai_kp.infrastructure.database.migrations.v0009_model_configuration import NAME, VERSION, migrate

__all__ = ["NAME", "VERSION", "migrate"]
