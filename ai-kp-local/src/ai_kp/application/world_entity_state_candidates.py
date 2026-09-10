"""Prompt-snapshot validation for AI-proposed campaign-world state changes."""

import json
from typing import Any

from ai_kp.application.errors import InvalidInputError
from ai_kp.director.turn_output import KpTurnOutput


def validate_world_entity_state_candidates(
    output: KpTurnOutput,
    included_sources: list[dict[str, Any]],
) -> KpTurnOutput:
    """Validate selection and bind concurrency versions from the prompt snapshot."""

    if not output.proposed_world_entity_states:
        return output
    available: dict[str, dict[str, Any]] = {}
    for source in included_sources:
        if source.get("kind") != "campaign_world_entity":
            continue
        try:
            content = json.loads(str(source.get("content") or "{}"))
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(content, dict) and content.get("entity_id"):
            available[str(content["entity_id"])] = content
    bound = []
    for candidate in output.proposed_world_entity_states:
        entity = available.get(candidate.entity_id)
        if entity is None:
            raise InvalidInputError(
                "AI proposed a world entity state for an entity absent from context"
            )
        version = entity.get("state_version")
        if type(version) is not int or version < 0:
            raise InvalidInputError("World entity context lacks a valid state version")
        dimensions = set(entity.get("state_dimensions") or ())
        if candidate.dimension not in dimensions:
            raise InvalidInputError(
                "AI proposed a world entity state dimension absent from context"
            )
        bound.append(candidate.model_copy(update={"expected_version": version}))
    return output.model_copy(update={"proposed_world_entity_states": bound})


__all__ = ["validate_world_entity_state_candidates"]
