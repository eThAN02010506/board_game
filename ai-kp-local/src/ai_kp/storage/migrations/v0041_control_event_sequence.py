"""Compatibility import for migration 41."""

from ai_kp.infrastructure.database.migrations.v0041_control_event_sequence import (
    NAME,
    VERSION,
    migrate,
)

__all__ = ["NAME", "VERSION", "migrate"]
