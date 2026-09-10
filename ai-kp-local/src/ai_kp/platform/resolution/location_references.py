"""Shared recognition of explicit location-valued state conditions."""

from __future__ import annotations

import re
from typing import Any

_PLAYER_LOCATION_PATHS = frozenset(
    {
        "scene_id",
        "location",
        "facts.location",
        "current_location",
        "current_location_id",
        "current_scene",
        "current_scene_id",
        "facts.current_location",
        "facts.current_location_id",
        "facts.current_scene",
        "facts.current_scene_id",
    }
)


def is_location_condition_path(path: str) -> bool:
    """Return whether a condition path explicitly stores a location identity."""

    normalized = re.sub(r"\[['\"]?([^\]'\"]+)['\"]?\]", r".\1", path).strip(".")
    return (
        normalized in _PLAYER_LOCATION_PATHS
        or normalized.endswith((".location", ".location_id"))
        or normalized.startswith("actor_locations.")
    )


def condition_references_location(
    path: str, value: Any, location_id: str
) -> bool:
    """Match only typed location paths, never arbitrary equal string values."""

    return value == location_id and is_location_condition_path(path)


__all__ = ["condition_references_location", "is_location_condition_path"]
