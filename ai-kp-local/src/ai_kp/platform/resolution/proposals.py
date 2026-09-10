"""Ruleset-neutral safety boundaries for proposed checks and world effects."""

from collections.abc import Sequence
from typing import Any


def validate_unresolved_check_boundary(
    proposed_checks: Sequence[Any],
    proposed_events: Sequence[Any],
    proposed_memories: Sequence[Any],
    proposed_npc_updates: Sequence[Any],
    proposed_map_moves: Sequence[Any],
    proposed_facts: Sequence[Any] = (),
    proposed_world_entity_states: Sequence[Any] = (),
) -> None:
    has_world_effects = any(
        (
            proposed_events,
            proposed_memories,
            proposed_npc_updates,
            proposed_map_moves,
            proposed_facts,
            proposed_world_entity_states,
        )
    )
    if proposed_checks and has_world_effects:
        raise ValueError(
            "A proposal requesting unresolved checks cannot also commit world effects"
        )


def validate_proposal_resolution_boundary(proposal: dict[str, Any]) -> None:
    validate_unresolved_check_boundary(
        proposal.get("proposed_checks") or (),
        proposal.get("proposed_events") or (),
        proposal.get("proposed_memories") or (),
        proposal.get("proposed_npc_updates") or (),
        proposal.get("proposed_map_moves") or (),
        proposal.get("proposed_facts") or (),
        proposal.get("proposed_world_entity_states") or (),
    )


__all__ = [
    "validate_proposal_resolution_boundary",
    "validate_unresolved_check_boundary",
]
