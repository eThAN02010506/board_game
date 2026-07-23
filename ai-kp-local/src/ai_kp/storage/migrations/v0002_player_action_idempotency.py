"""Compatibility import for the migrated schema migration."""
from ai_kp.infrastructure.database.migrations.v0002_player_action_idempotency import NAME, VERSION, migrate

__all__ = ["NAME", "VERSION", "migrate"]
