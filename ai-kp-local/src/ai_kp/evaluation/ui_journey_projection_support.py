"""Shared, side-effect-free helpers for strict UI journey projection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


def projection_observation(
    observed_at: Any, kind: str, session_id: str, **fields: Any
) -> dict[str, Any]:
    return {
        "observed_at": projection_timestamp(observed_at),
        "kind": kind,
        "session_id": session_id,
        **fields,
    }


def projection_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("gameplay authority record lacks a timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def json_array(value: Any) -> list[Any]:
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def sql_placeholders(values: tuple[str, ...]) -> str:
    if not values:
        raise ValueError("SQL projection requires at least one identifier")
    return ",".join("?" for _ in values)


def applied_effect_ids(batch: Mapping[str, Any]) -> list[str]:
    commands = batch.get("commands")
    if not isinstance(commands, list):
        return []
    return [
        f"{batch['id']}:command:{index}"
        for index, command in enumerate(commands, start=1)
        if isinstance(command, Mapping)
        and not (
            command.get("kind") == "emit_event"
            and command.get("event_type") == "action_resolved"
        )
    ]


__all__ = [
    "applied_effect_ids",
    "canonical_hash",
    "json_array",
    "json_object",
    "projection_observation",
    "projection_timestamp",
    "sql_placeholders",
]
