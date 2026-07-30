"""Compatibility import for migration 40."""

from ai_kp.infrastructure.database.migrations.v0040_opposed_rerolls import (
    NAME,
    VERSION,
    migrate,
)

__all__ = ["NAME", "VERSION", "migrate"]
