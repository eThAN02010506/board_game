"""Deterministic fingerprints and AI-safe snapshots for terminal skill checks."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterable, Mapping
from hashlib import sha256
from math import isfinite
from typing import Any

CHECK_CONSEQUENCE_SCHEMA_VERSION = "check-consequence.v1"
HIDDEN_CHECK_PUBLIC_NARRATION = "局势仍在发展，当前没有可公开确认的新信息。"

_TERMINAL_STATUSES = frozenset({"resolved", "overridden", "cancelled"})
_MAX_JSON_DEPTH = 32
_MAX_JSON_NODES = 20_000
_MAX_CONTAINER_ITEMS = 4_096
_MAX_STRING_LENGTH = 100_000
_MAX_INTEGER_BITS = 4_096


def _required_text(value: Any, *, field_name: str, collapse: bool = False) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be text")
    normalized = unicodedata.normalize("NFKC", value)
    normalized = " ".join(normalized.split()) if collapse else normalized.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    if len(normalized) > _MAX_STRING_LENGTH:
        raise ValueError(f"{field_name} is too long")
    return normalized


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name=field_name)


def _optional_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an integer")
    return value


def _normalize_json(
    value: Any,
    *,
    field_name: str,
    depth: int = 0,
    ancestors: tuple[int, ...] = (),
    node_count: list[int] | None = None,
) -> Any:
    counter = node_count if node_count is not None else [0]
    counter[0] += 1
    if counter[0] > _MAX_JSON_NODES:
        raise ValueError(f"{field_name} exceeds the JSON node limit")
    if depth > _MAX_JSON_DEPTH:
        raise ValueError(f"{field_name} exceeds the JSON depth limit")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if value.bit_length() > _MAX_INTEGER_BITS:
            raise ValueError(f"{field_name} contains an oversized integer")
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError(f"{field_name} must contain finite JSON data")
        return 0.0 if value == 0 else value
    if type(value) is str:
        if len(value) > _MAX_STRING_LENGTH:
            raise ValueError(f"{field_name} contains an oversized string")
        return value
    if type(value) not in {dict, list, tuple}:
        raise ValueError(f"{field_name} must contain only JSON data")

    container_id = id(value)
    if container_id in ancestors:
        raise ValueError(f"{field_name} cannot contain cycles")
    if len(value) > _MAX_CONTAINER_ITEMS:
        raise ValueError(f"{field_name} contains an oversized collection")
    child_ancestors = (*ancestors, container_id)
    if type(value) in {list, tuple}:
        return [
            _normalize_json(
                item,
                field_name=field_name,
                depth=depth + 1,
                ancestors=child_ancestors,
                node_count=counter,
            )
            for item in value
        ]

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if type(key) is not str:
            raise ValueError(f"{field_name} object keys must be strings")
        if len(key) > _MAX_STRING_LENGTH:
            raise ValueError(f"{field_name} contains an oversized object key")
        normalized[key] = _normalize_json(
            item,
            field_name=field_name,
            depth=depth + 1,
            ancestors=child_ancestors,
            node_count=counter,
        )
    return normalized


def _json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{field_name} must be an object")
    normalized = _normalize_json(value, field_name=field_name)
    if not isinstance(normalized, dict):
        raise ValueError(f"{field_name} must be an object")  # noqa: TRY004
    return normalized


def _optional_json_object(
    value: Any,
    *,
    field_name: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    return _json_object(value, field_name=field_name)


def _normalize_status(value: Any) -> str:
    status = _required_text(value, field_name="status").casefold()
    if status == "requested":
        raise ValueError("Requested checks have no consequence snapshot")
    if status not in _TERMINAL_STATUSES:
        raise ValueError(f"Unknown skill check status: {status}")
    return status


def _normalize_check(check: Mapping[str, Any]) -> dict[str, Any]:
    if type(check) is not dict:
        raise ValueError("Each skill check must be a dict")

    check_id = _required_text(check.get("id"), field_name="id")
    status = _normalize_status(check.get("status"))
    proposal_id = _optional_text(check.get("proposal_id"), field_name="proposal_id")
    player_action_id = _optional_text(
        check.get("player_action_id"),
        field_name="player_action_id",
    )
    if proposal_id is None and player_action_id is None:
        raise ValueError(
            "Skill checks require a proposal_id or player_action_id origin"
        )

    raw_dice = _optional_json_object(
        check.get("raw_dice"),
        field_name=f"{check_id}.raw_dice",
    )
    selected_roll = _optional_int(
        check.get("selected_roll"),
        field_name=f"{check_id}.selected_roll",
    )
    threshold = _optional_int(
        check.get("threshold"),
        field_name=f"{check_id}.threshold",
    )
    success_level = _optional_text(
        check.get("success_level"),
        field_name=f"{check_id}.success_level",
    )
    passed = check.get("passed")
    if passed is not None and type(passed) is not bool:
        raise ValueError(f"{check_id}.passed must be a boolean")

    override_reason = _optional_text(
        check.get("override_reason"),
        field_name=f"{check_id}.override_reason",
    )
    original_result = _optional_json_object(
        check.get("original_result"),
        field_name=f"{check_id}.original_result",
    )
    input_method = _optional_text(
        check.get("input_method"),
        field_name=f"{check_id}.input_method",
    )

    result_values = (
        input_method,
        raw_dice,
        selected_roll,
        threshold,
        success_level,
        passed,
    )
    if status in {"resolved", "overridden"} and any(
        value is None for value in result_values
    ):
        raise ValueError(f"{status} check {check_id} requires a complete result")
    if status == "cancelled" and any(value is not None for value in result_values):
        raise ValueError(f"cancelled check {check_id} cannot contain a result")
    if status == "overridden":
        if override_reason is None or original_result is None:
            raise ValueError(
                f"overridden check {check_id} requires override audit data"
            )
    elif override_reason is not None or original_result is not None:
        raise ValueError(
            f"{status} check {check_id} cannot contain override audit data"
        )

    target = _optional_int(check.get("target"), field_name=f"{check_id}.target")
    if target is None:
        raise ValueError(f"{check_id}.target is required")
    bonus_dice = _optional_int(
        check.get("bonus_dice"),
        field_name=f"{check_id}.bonus_dice",
    )
    if bonus_dice is None:
        raise ValueError(f"{check_id}.bonus_dice is required")
    hidden = check.get("hidden")
    if type(hidden) is not bool:
        raise ValueError(f"{check_id}.hidden must be a boolean")

    return {
        "id": check_id,
        "status": status,
        "hidden": hidden,
        "origin": {
            "proposal_id": proposal_id,
            "player_action_id": player_action_id,
        },
        "skill": {
            "key": _optional_text(
                check.get("skill_key"),
                field_name=f"{check_id}.skill_key",
            ),
            "name": _required_text(
                check.get("skill_name"),
                field_name=f"{check_id}.skill_name",
                collapse=True,
            ),
            "target": target,
            "difficulty": _required_text(
                check.get("difficulty"),
                field_name=f"{check_id}.difficulty",
            ),
            "bonus_dice": bonus_dice,
        },
        "raw_result": {
            "input_method": input_method,
            "raw_dice": raw_dice,
            "selected_roll": selected_roll,
            "threshold": threshold,
        },
        "success_level": success_level,
        "passed": passed,
        "override": {
            "reason": override_reason,
            "original_result": original_result,
        },
        "pushed_from_check_id": _optional_text(
            check.get("pushed_from_check_id"),
            field_name=f"{check_id}.pushed_from_check_id",
        ),
        "ruleset": {
            "id": _required_text(
                check.get("ruleset_id"),
                field_name=f"{check_id}.ruleset_id",
            ),
            "version": _required_text(
                check.get("ruleset_version"),
                field_name=f"{check_id}.ruleset_version",
            ),
        },
        "source_reference": _json_object(
            check.get("source_reference"),
            field_name=f"{check_id}.source_reference",
        ),
    }


def _prepare_checks(
    checks: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[str]]]:
    normalized = [_normalize_check(check) for check in checks]
    if not normalized:
        raise ValueError("At least one skill check is required")

    by_id: dict[str, dict[str, Any]] = {}
    for check in normalized:
        if check["id"] in by_id:
            raise ValueError(f"Duplicate skill check id: {check['id']}")
        by_id[check["id"]] = check

    origin = normalized[0]["origin"]
    if any(check["origin"] != origin for check in normalized[1:]):
        raise ValueError("Skill checks must share one proposal/action origin")

    children: dict[str, list[str]] = {check_id: [] for check_id in by_id}
    for check in normalized:
        parent_id = check["pushed_from_check_id"]
        if parent_id is None:
            continue
        if parent_id not in by_id:
            raise ValueError(
                f"Push chain is missing parent check: {parent_id}"
            )
        children[parent_id].append(check["id"])

    for parent_id, child_ids in children.items():
        if len(child_ids) > 1:
            raise ValueError(f"Push check {parent_id} has multiple children")
        if not child_ids:
            continue
        parent = by_id[parent_id]
        if parent["status"] not in {"resolved", "overridden"}:
            raise ValueError("Only a resolved check can be a push parent")
        if parent["passed"] is not False:
            raise ValueError("Only a failed check can be a push parent")

    roots = sorted(
        check_id
        for check_id, check in by_id.items()
        if check["pushed_from_check_id"] is None
    )
    ordered: list[dict[str, Any]] = []
    visited: set[str] = set()
    for root_id in roots:
        current_id: str | None = root_id
        while current_id is not None:
            if current_id in visited:
                raise ValueError("Push chain contains a cycle")
            visited.add(current_id)
            ordered.append(by_id[current_id])
            current_children = children[current_id]
            current_id = current_children[0] if current_children else None
    if len(visited) != len(by_id):
        raise ValueError("Push chain contains a cycle")
    return origin, ordered, children


def _canonical_payload(
    checks: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[str]]]:
    origin, ordered, children = _prepare_checks(checks)
    return (
        {
            "schema_version": CHECK_CONSEQUENCE_SCHEMA_VERSION,
            "origin": origin,
            "checks": ordered,
        },
        ordered,
        children,
    )


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def check_consequence_fingerprint(
    checks: Iterable[Mapping[str, Any]],
) -> str:
    """Hash every terminal row, including non-leaf rows in push chains."""

    payload, _ordered, _children = _canonical_payload(checks)
    return _payload_fingerprint(payload)


def build_check_consequence_snapshot(
    checks: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return leaf-only effective results safe to include as AI data context."""

    payload, ordered, children = _canonical_payload(checks)
    by_id = {check["id"]: check for check in ordered}
    effective_results: list[dict[str, Any]] = []
    for check in ordered:
        if children[check["id"]]:
            continue
        chain_ids = [check["id"]]
        parent_id = check["pushed_from_check_id"]
        while parent_id is not None:
            chain_ids.append(parent_id)
            parent_id = by_id[parent_id]["pushed_from_check_id"]
        chain_ids.reverse()
        effective_results.append(
            {
                "check_id": check["id"],
                "status": check["status"],
                "hidden": check["hidden"],
                "skill_name": check["skill"]["name"],
                "target": check["skill"]["target"],
                "difficulty": check["skill"]["difficulty"],
                "selected_roll": check["raw_result"]["selected_roll"],
                "threshold": check["raw_result"]["threshold"],
                "success_level": check["success_level"],
                "passed": check["passed"],
                "was_overridden": check["status"] == "overridden",
                "push_chain_ids": chain_ids,
                "ruleset": check["ruleset"],
            }
        )
    return {
        "schema_version": CHECK_CONSEQUENCE_SCHEMA_VERSION,
        "origin": payload["origin"],
        "result_fingerprint": _payload_fingerprint(payload),
        "has_hidden_checks": any(check["hidden"] for check in ordered),
        "effective_results": effective_results,
    }


__all__ = [
    "CHECK_CONSEQUENCE_SCHEMA_VERSION",
    "HIDDEN_CHECK_PUBLIC_NARRATION",
    "build_check_consequence_snapshot",
    "check_consequence_fingerprint",
]
