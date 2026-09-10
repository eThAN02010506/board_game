"""Strict JSON validation and deterministic bounded JSON projection.

This module deliberately has no scenario-domain dependencies.  Authoritative
state may cross process and persistence boundaries, so accepting Python-only
values (sets, non-string mapping keys, or non-finite floats) would make hashes
and serialized payloads ambiguous.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

DIRECTOR_JSON_VALUE_BYTES = 1024
DIRECTOR_JSON_MAX_DEPTH = 4
DIRECTOR_JSON_CONTAINER_ITEMS = 12
DIRECTOR_JSON_STRING_CHARS = 512


def validate_strict_json(
    value: Any,
    *,
    path: str = "$",
    max_depth: int | None = None,
    max_nodes: int | None = None,
    max_string_bytes: int | None = None,
) -> None:
    """Reject values that cannot be represented as unambiguous strict JSON.

    Validation is iterative so a cyclic or extremely deep Python value fails
    clearly instead of relying on the interpreter recursion limit.  Reusing the
    same child container in two branches is allowed; only active-path cycles are
    rejected.
    """

    if max_depth is not None and max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    if max_nodes is not None and max_nodes < 1:
        raise ValueError("max_nodes must be positive")
    if max_string_bytes is not None and max_string_bytes < 0:
        raise ValueError("max_string_bytes must be non-negative")

    active_containers: set[int] = set()
    stack: list[tuple[Any, str, bool, int]] = [(value, path, False, 0)]
    visited_nodes = 0
    while stack:
        current, current_path, leaving, depth = stack.pop()
        if leaving:
            active_containers.remove(id(current))
            continue
        visited_nodes += 1
        if max_nodes is not None and visited_nodes > max_nodes:
            raise ValueError(f"{path} exceeds the JSON node limit of {max_nodes}")
        if max_depth is not None and depth > max_depth:
            raise ValueError(f"{current_path} exceeds the JSON depth limit of {max_depth}")
        if current is None or isinstance(current, (bool, int)):
            continue
        if isinstance(current, str):
            _validate_json_string(
                current,
                path=current_path,
                max_string_bytes=max_string_bytes,
            )
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                raise ValueError(f"{current_path} must contain only finite JSON numbers")
            continue
        if isinstance(current, dict):
            container_id = id(current)
            if container_id in active_containers:
                raise ValueError(f"{current_path} contains a cyclic JSON object")
            active_containers.add(container_id)
            stack.append((current, current_path, True, depth))
            children: list[tuple[Any, str, bool, int]] = []
            for key, child in current.items():
                if not isinstance(key, str):
                    raise TypeError(f"{current_path} contains a non-string JSON object key")
                _validate_json_string(
                    key,
                    path=f"{current_path} object key",
                    max_string_bytes=max_string_bytes,
                )
                children.append((child, _child_path(current_path, key), False, depth + 1))
            stack.extend(reversed(children))
            continue
        if isinstance(current, list):
            container_id = id(current)
            if container_id in active_containers:
                raise ValueError(f"{current_path} contains a cyclic JSON array")
            active_containers.add(container_id)
            stack.append((current, current_path, True, depth))
            for index in range(len(current) - 1, -1, -1):
                stack.append((current[index], f"{current_path}[{index}]", False, depth + 1))
            continue
        raise TypeError(
            f"{current_path} contains unsupported JSON value type {type(current).__name__}"
        )


def canonical_json_bytes(
    value: Any,
    *,
    max_depth: int | None = None,
    max_nodes: int | None = None,
    max_string_bytes: int | None = None,
) -> bytes:
    """Encode one validated JSON value with stable keys and strict numbers."""

    validate_strict_json(
        value,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_string_bytes=max_string_bytes,
    )
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        raise ValueError("Value cannot be encoded as canonical JSON") from exc


def json_byte_size(value: Any) -> int:
    return len(canonical_json_bytes(value))


def shorten_json_string(
    value: str,
    *,
    json_budget: int,
    max_chars: int | None = None,
) -> tuple[str, bool]:
    """Fit a string into a JSON byte budget, retaining a stable hash suffix."""

    if json_budget < 2:
        raise ValueError("JSON string budget must include two quote bytes")
    if (max_chars is None or len(value) <= max_chars) and json_byte_size(value) <= json_budget:
        return value, False

    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    suffix = f"…#{digest}"
    if json_byte_size(suffix) > json_budget:
        # Current callers use substantially larger budgets.  Keeping this
        # deterministic fallback makes the helper safe for future small ones.
        suffix = digest[: max(0, json_budget - 2)]
    low = 0
    high = len(value)
    if max_chars is not None:
        high = min(high, max(0, max_chars - len(suffix)))
    best = suffix
    while low <= high:
        middle = (low + high) // 2
        candidate = value[:middle] + suffix
        if json_byte_size(candidate) <= json_budget:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best, True


def bounded_json_value(
    value: Any,
    *,
    json_budget: int,
    depth: int = 0,
) -> tuple[Any, bool]:
    """Project strict JSON with bounded width, depth, strings, and bytes."""

    if isinstance(value, str):
        return shorten_json_string(
            value,
            json_budget=json_budget,
            max_chars=DIRECTOR_JSON_STRING_CHARS,
        )

    if value is None or isinstance(value, (bool, int, float)):
        try:
            if json_byte_size(value) <= json_budget:
                return value, False
        except ValueError:
            pass
        if isinstance(value, bool):
            return False, True
        if isinstance(value, int):
            return 0, True
        if isinstance(value, float):
            return 0.0, True
        return None, True

    if isinstance(value, dict):
        if depth >= DIRECTOR_JSON_MAX_DEPTH:
            return {}, bool(value)
        projected: dict[str, Any] = {}
        used_bytes = 2
        keys = sorted(value)
        truncated = len(keys) > DIRECTOR_JSON_CONTAINER_ITEMS
        for key in keys[:DIRECTOR_JSON_CONTAINER_ITEMS]:
            key_size = json_byte_size(key)
            structural_bytes = (1 if projected else 0) + key_size + 1
            remaining = json_budget - used_bytes - structural_bytes
            if remaining < 2:
                truncated = True
                continue
            child, child_truncated = bounded_json_value(
                value[key],
                json_budget=remaining,
                depth=depth + 1,
            )
            child_size = json_byte_size(child)
            if child_size > remaining:
                truncated = True
                continue
            projected[key] = child
            used_bytes += structural_bytes + child_size
            truncated = truncated or child_truncated
        return projected, truncated

    if isinstance(value, list):
        if depth >= DIRECTOR_JSON_MAX_DEPTH:
            return [], bool(value)
        projected_sequence: list[Any] = []
        truncated = len(value) > DIRECTOR_JSON_CONTAINER_ITEMS
        used_bytes = 2
        for child_value in value[:DIRECTOR_JSON_CONTAINER_ITEMS]:
            structural_bytes = 1 if projected_sequence else 0
            remaining = json_budget - used_bytes - structural_bytes
            if remaining < 2:
                truncated = True
                continue
            child, child_truncated = bounded_json_value(
                child_value,
                json_budget=remaining,
                depth=depth + 1,
            )
            child_size = json_byte_size(child)
            if child_size > remaining:
                truncated = True
                continue
            projected_sequence.append(child)
            used_bytes += structural_bytes + child_size
            truncated = truncated or child_truncated
        return projected_sequence, truncated

    raise TypeError(f"Unsupported bounded JSON value type: {type(value).__name__}")


def searchable_json(value: Any, *, json_budget: int) -> str:
    projected, _ = bounded_json_value(value, json_budget=json_budget)
    return canonical_json_bytes(projected).decode("utf-8")


def _child_path(parent: str, key: str) -> str:
    preview = key if len(key) <= 80 else key[:77] + "…"
    return f"{parent}.{preview}"


def _validate_json_string(
    value: str,
    *,
    path: str,
    max_string_bytes: int | None,
) -> None:
    if max_string_bytes is not None and len(value) > max_string_bytes:
        raise ValueError(f"{path} exceeds the JSON string byte limit of {max_string_bytes}")
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError(f"{path} is not valid UTF-8 text") from exc
    if max_string_bytes is not None and encoded_length > max_string_bytes:
        raise ValueError(f"{path} exceeds the JSON string byte limit of {max_string_bytes}")


__all__ = [
    "DIRECTOR_JSON_CONTAINER_ITEMS",
    "DIRECTOR_JSON_MAX_DEPTH",
    "DIRECTOR_JSON_STRING_CHARS",
    "DIRECTOR_JSON_VALUE_BYTES",
    "bounded_json_value",
    "canonical_json_bytes",
    "json_byte_size",
    "searchable_json",
    "shorten_json_string",
    "validate_strict_json",
]
