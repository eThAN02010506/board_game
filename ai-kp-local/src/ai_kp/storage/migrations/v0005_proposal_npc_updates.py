"""Compatibility import for the migrated schema migration."""
from ai_kp.infrastructure.database.migrations.v0005_proposal_npc_updates import (
    NAME,
    VERSION,
    migrate,
)

__all__ = ["NAME", "VERSION", "migrate"]
