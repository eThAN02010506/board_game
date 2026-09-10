"""Shared exclusive assignments for contract validation and parallel settlement."""

import json

from .contracts import WorldCommand


def exclusive_assignments(command: WorldCommand) -> tuple[tuple[tuple[str, str], str], ...]:
    """Describe replacement writes; additive memory/resources remain independent.

    Encode values as JSON so booleans do not compare equal to integers, and tag
    deletion separately so a legitimate string value cannot impersonate removal.
    """
    if command.kind == "set_world_entity_state":
        return ((("world_entity", f"{command.entity_id}.{command.path}"),
                 _value([command.value, command.payload["visibility"]])),)
    if command.kind == "update_entity_runtime":
        return tuple(
            (("entity_runtime", f"{command.entity_id}.{field}"), _value(value))
            for field, value in command.payload.items()
            if field != "add_memory_ref"
        )
    target = None
    if command.kind in {"set_fact", "remove_fact"}:
        target = ("fact", str(command.path))
    elif command.kind == "set_entity_status":
        target = ("entity", str(command.entity_id))
    elif command.kind == "move_actor":
        target = ("actor", str(command.actor_id))
    elif command.kind == "set_scene":
        target = ("scene", "scene_id")
    elif command.kind == "complete_run":
        target = ("run", "ending_id")
    if target is None:
        return ()
    return ((target, "delete" if command.kind == "remove_fact" else _value(command.value)),)


def _value(value: object) -> str:
    return "set:" + json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
