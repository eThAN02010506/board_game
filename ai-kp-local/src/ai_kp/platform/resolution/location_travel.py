"""Canonical executable operators derived from published location links."""

from __future__ import annotations

import hashlib
import json

from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    LocationLink,
    ScenarioContract,
    StateCondition,
    WorldCommand,
)


class CanonicalTravelOperatorConflict(ValueError):
    """A reserved canonical travel identity is occupied by different content."""


def canonical_travel_operator_id(
    link: LocationLink, *, origin: str, destination: str
) -> str:
    """Return a stable identity based only on executable link semantics."""

    conditions = sorted(item.model_dump_json() for item in link.preconditions)
    payload = json.dumps(
        [origin, destination, conditions],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "travel-link-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_travel_operator_ids(link: LocationLink) -> tuple[str, ...]:
    """Enumerate only the reserved identities derived from one exact link."""

    directions = [(link.from_location_id, link.to_location_id)]
    if not link.one_way:
        directions.append((link.to_location_id, link.from_location_id))
    return tuple(
        canonical_travel_operator_id(
            link, origin=origin, destination=destination
        )
        for origin, destination in directions
    )


def materialize_location_travel_operators(
    contract: ScenarioContract,
) -> ScenarioContract:
    """Return a canonical contract where every link direction is actionable."""

    titles = {item.location_id: item.title for item in contract.locations}
    generated: list[ActionOperator] = []
    generated_indexes: dict[str, int] = {}
    for link in contract.location_links:
        directions = [(link.from_location_id, link.to_location_id)]
        if not link.one_way:
            directions.append((link.to_location_id, link.from_location_id))
        for origin, destination in directions:
            operator_id = canonical_travel_operator_id(
                link, origin=origin, destination=destination
            )
            origin_title = titles[origin]
            destination_title = titles[destination]
            route_title = f"{origin_title} → {destination_title}"
            hints = tuple(
                dict.fromkeys((destination_title[:160], route_title[:160]))
            )
            operator = ActionOperator(
                operator_id=operator_id,
                title=route_title[:240],
                intent_hints=hints,
                policy="automatic",
                preconditions=tuple(
                    dict.fromkeys(
                        (
                            StateCondition(
                                path="scene_id", operator="eq", value=origin
                            ),
                            *link.preconditions,
                        )
                    )
                ),
                success_commands=(
                    WorldCommand(kind="set_scene", value=destination),
                ),
                rationale="Canonical movement authorized by a published location link.",
                maximum_effect=f"Move the active scene to {destination_title}.",
                source_refs=link.source_refs,
            )
            previous_index = generated_indexes.get(operator_id)
            if previous_index is None:
                generated_indexes[operator_id] = len(generated)
                generated.append(operator)
                continue
            previous = generated[previous_index]
            refs = tuple(dict.fromkeys((*previous.source_refs, *operator.source_refs)))[:16]
            generated[previous_index] = previous.model_copy(
                update={"source_refs": refs}
            )

    merged = list(contract.operators)
    existing = {
        item.operator_id: (index, item)
        for index, item in enumerate(contract.operators)
    }
    additions = []
    for operator in generated:
        existing_entry = existing.get(operator.operator_id)
        if existing_entry is None:
            additions.append(operator)
            continue
        previous_index, previous = existing_entry
        if previous.model_dump(mode="json", exclude={"source_refs"}) != (
            operator.model_dump(mode="json", exclude={"source_refs"})
        ):
            raise CanonicalTravelOperatorConflict(
                "Published operator conflicts with canonical location travel: "
                + operator.operator_id
            )
        refs = tuple(dict.fromkeys((*previous.source_refs, *operator.source_refs)))[:16]
        merged[previous_index] = previous.model_copy(update={"source_refs": refs})
    if not additions:
        operators = tuple(merged)
    else:
        operators = (*merged, *additions)
    if operators == contract.operators:
        return contract
    return contract.model_copy(update={"operators": operators})


__all__ = [
    "CanonicalTravelOperatorConflict",
    "canonical_travel_operator_id",
    "canonical_travel_operator_ids",
    "materialize_location_travel_operators",
]
