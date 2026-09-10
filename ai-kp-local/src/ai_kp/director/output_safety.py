"""Narrow, reductive repair for unsafe structured proposal combinations."""

from __future__ import annotations

import json

from ai_kp.platform.structured_json import decode_json_object

WORLD_EFFECT_FIELDS = (
    "proposed_events",
    "proposed_memories",
    "proposed_npc_updates",
    "proposed_map_moves",
    "proposed_facts",
    "proposed_world_entity_states",
)


def remove_precommitted_world_effects(raw: str) -> str:
    """Remove effects only when the candidate itself declares an unresolved roll."""
    try:
        payload = decode_json_object(raw)
    except ValueError:
        return raw
    if not isinstance(payload, dict):
        return raw
    ruling = payload.get("action_ruling")
    checks = payload.get("proposed_checks")
    if not isinstance(ruling, dict) or not isinstance(checks, list):
        return raw
    resolution = ruling.get("resolution")
    feasibility = ruling.get("feasibility")
    must_remove_effects = bool(checks) and resolution in {"check", "opposed"}
    impossible = feasibility == "impossible" and resolution == "no_roll"
    if not must_remove_effects and not impossible:
        return raw

    changed = False
    if impossible and checks:
        payload["proposed_checks"] = []
        changed = True
    for field in WORLD_EFFECT_FIELDS:
        value = payload.get(field)
        if isinstance(value, list) and value:
            payload[field] = []
            changed = True
    if not changed:
        return raw
    return json.dumps(payload, ensure_ascii=False)


__all__ = ["WORLD_EFFECT_FIELDS", "remove_precommitted_world_effects"]
