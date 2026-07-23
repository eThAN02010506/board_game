"""SQLite row decoding and JSON field helpers."""

import json
import sqlite3
from typing import Any


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a SQLite row without changing its stored value types."""

    return dict(row)


def decode_json_field(value: str, fallback: Any) -> Any:
    """Decode a JSON column while preserving the repository's legacy fallback."""

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback
