"""Pure CoC7 runtime resource, skill, and armor transitions."""

from __future__ import annotations

from typing import Any, Literal

AdjustmentDirection = Literal["gain", "loss"]


def adjustment_sign(direction: str) -> int:
    if direction == "gain":
        return 1
    if direction == "loss":
        return -1
    raise ValueError("Adjustment direction must be gain or loss")


def adjust_resource(
    state: dict[str, Any],
    canonical: dict[str, Any],
    *,
    resource: str,
    direction: str,
    amount: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state_key = {
        "san": "current_san",
        "mp": "current_mp",
        "luck": "current_luck",
    }.get(resource)
    if state_key is None:
        raise ValueError("Unsupported CoC7 character resource")
    derived = canonical.get("derived") or {}
    maximum = {
        "san": int(derived.get("max_san", 99)),
        "mp": int(derived.get("max_mp") or 0),
        "luck": 99,
    }[resource]
    old_value = int(state[state_key])
    new_value = min(maximum, max(0, old_value + adjustment_sign(direction) * amount))
    return (
        {
            "resource": resource,
            "old_value": old_value,
            "new_value": new_value,
            "applied_delta": new_value - old_value,
        },
        {state_key: new_value},
    )


def active_armor(conditions: list[dict[str, Any]]) -> int:
    values = [
        int(item.get("value") or 0)
        for item in conditions
        if item.get("type") == "armor" and item.get("active", True)
    ]
    if len(values) > 1 or any(value < 0 or value > 1_000 for value in values):
        raise ValueError("Character armor state is invalid")
    return values[0] if values else 0


def adjust_armor(
    conditions: list[dict[str, Any]], *, direction: str, amount: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    old_value = active_armor(conditions)
    new_value = min(1_000, max(0, old_value + adjustment_sign(direction) * amount))
    updated = [item for item in conditions if item.get("type") != "armor"]
    if new_value:
        updated.append({"type": "armor", "active": True, "value": new_value})
    return (
        {
            "old_value": old_value,
            "new_value": new_value,
            "applied_delta": new_value - old_value,
        },
        {"conditions": updated},
    )


def runtime_skill_target(skill: dict[str, Any], conditions: list[dict[str, Any]]) -> int:
    return min(
        100,
        max(
            0,
            int(skill.get("current_value") or 0)
            + runtime_skill_adjustment(skill, conditions),
        ),
    )


def runtime_skill_adjustment(
    skill: dict[str, Any], conditions: list[dict[str, Any]]
) -> int:
    skill_key = str(skill.get("skill_key") or "")
    specialization = str(skill.get("specialization") or "")
    adjustment = sum(
        int(item.get("delta") or 0)
        for item in conditions
        if item.get("type") == "skill_adjustment"
        and item.get("active", True)
        and str(item.get("skill_key") or "") == skill_key
        and str(item.get("specialization") or "") == specialization
    )
    return adjustment


def adjust_skill(
    conditions: list[dict[str, Any]],
    canonical: dict[str, Any],
    *,
    requested_skill: str,
    direction: str,
    amount: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    wanted = requested_skill.strip().casefold()
    matches = [
        item
        for item in canonical.get("skills") or []
        if wanted
        in {
            str(item.get("skill_key") or "").strip().casefold(),
            str(item.get("display_name") or "").strip().casefold(),
            str(item.get("specialization") or "").strip().casefold(),
        }
    ]
    if len(matches) != 1:
        raise ValueError("Skill effect must match exactly one approved skill")
    skill = matches[0]
    skill_key = str(skill.get("skill_key") or "").strip()
    specialization = str(skill.get("specialization") or "").strip()
    base_value = int(skill.get("current_value") or 0)
    old_value = runtime_skill_target(skill, conditions)
    new_value = min(100, max(0, old_value + adjustment_sign(direction) * amount))
    new_modifier = new_value - base_value
    updated = [
        item
        for item in conditions
        if not (
            item.get("type") == "skill_adjustment"
            and str(item.get("skill_key") or "") == skill_key
            and str(item.get("specialization") or "") == specialization
        )
    ]
    if new_modifier:
        updated.append(
            {
                "type": "skill_adjustment",
                "active": True,
                "skill_key": skill_key,
                "specialization": specialization or None,
                "delta": new_modifier,
            }
        )
    return (
        {
            "skill_key": skill_key,
            "specialization": specialization or None,
            "old_value": old_value,
            "new_value": new_value,
            "applied_delta": new_value - old_value,
        },
        {"conditions": updated},
    )


__all__ = [
    "AdjustmentDirection",
    "active_armor",
    "adjust_armor",
    "adjust_resource",
    "adjust_skill",
    "adjustment_sign",
    "runtime_skill_adjustment",
    "runtime_skill_target",
]
