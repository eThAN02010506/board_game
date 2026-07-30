"""Compatibility import for migration 39."""

from ai_kp.infrastructure.database.migrations.v0039_simulation_evals import (
    NAME,
    VERSION,
    migrate,
)

__all__ = ["NAME", "VERSION", "migrate"]
