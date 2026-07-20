import sqlite3

from ai_kp.kp.context_repository import ContextAssemblyRepository
from ai_kp.maps.repository import MapRepository
from ai_kp.realtime.repository import RealtimeRepository
from ai_kp.security.repository import SecurityRepository
from ai_kp.storage.repositories.turns import TurnRepository
from ai_kp.storage.repositories.world import WorldRepository
from ai_kp.storage.rows import decode_json_field, row_to_dict

__all__ = ["Repository", "decode_json_field", "row_to_dict"]


class Repository(
    WorldRepository,
    TurnRepository,
    MapRepository,
    ContextAssemblyRepository,
    SecurityRepository,
    RealtimeRepository,
):
    """Backward-compatible facade over the feature-specific SQLite repositories."""

    def __init__(self, connection: sqlite3.Connection):
        super().__init__(connection)
