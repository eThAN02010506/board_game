"""Compatibility import for migration 36."""

from ai_kp.infrastructure.database.migrations.v0036_director_control import (
    NAME,
    VERSION,
    migrate,
)

__all__ = ["NAME", "VERSION", "migrate"]
