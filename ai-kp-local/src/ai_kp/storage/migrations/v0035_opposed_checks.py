"""Compatibility import for migration 35."""

from ai_kp.infrastructure.database.migrations.v0035_opposed_checks import (
    NAME,
    VERSION,
    migrate,
)

__all__ = ["NAME", "VERSION", "migrate"]
