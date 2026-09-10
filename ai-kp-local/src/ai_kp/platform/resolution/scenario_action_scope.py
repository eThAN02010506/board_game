"""Evidence-bounded player-action location scope normalization."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from ai_kp.platform.resolution.contracts import StateCondition
from ai_kp.platform.resolution.scenario_ir_models import (
    IrAction,
    IrLocation,
    ScenarioIrBatch,
)
from ai_kp.platform.resolution.scenario_scene_graph_supplement import (
    SceneGraphSourceContext,
    opening_scene_section_key,
)
from ai_kp.platform.resolution.scenario_terminal_observation import (
    is_source_terminal_observation_action,
    is_terminal_observation_fact_command,
)


@dataclass(frozen=True)
class ActionScopeLocation:
    """Server-owned location identity exposed only to the action-scope gate."""

    id: str
    title: str


def resolve_partition_action_location_slots(
    batches: tuple[ScenarioIrBatch, ...], assumptions: list[str]
) -> tuple[ScenarioIrBatch, ...]:
    """Resolve opaque model slots while their partition-owned catalog exists."""

    resolved: list[ScenarioIrBatch] = []
    for batch in batches:
        actions: list[IrAction] = []
        for action in batch.actions:
            if action.location_slot is None:
                actions.append(action)
                continue
            if action.location_slot >= len(batch.locations):
                assumptions.append(
                    f"Action with unknown location slot was discarded: {action.id}"
                )
                continue
            actions.append(
                action.model_copy(
                    update={
                        "location_slot": None,
                        "location_id": batch.locations[action.location_slot].id,
                    }
                )
            )
        resolved.append(batch.model_copy(update={"actions": tuple(actions)}))
    return tuple(resolved)


def bind_action_location_scope(
    action: IrAction,
    *,
    canonical_conditions: tuple[StateCondition, ...],
    location_by_id: Mapping[str, IrLocation | ActionScopeLocation],
    source_texts: dict[str, str] | None,
    source_titles: dict[str, str] | None,
    source_section_paths: dict[str, tuple[str, ...]] | None,
    source_scene_keys: dict[str, str] | None,
    proven_initial_scene_id: str | None,
    infer_unselected_location: bool,
    assumptions: list[str],
) -> IrAction | None:
    """Bind one source-grounded action to an authoritative scene.

    Programmatic assembly without evidence text remains backwards compatible.
    Evidence-backed authoring must select one declared location, or prove that
    the source explicitly makes the action location-independent.
    """

    location_ids = set(location_by_id)
    has_terminal_observation_fact = any(
        is_terminal_observation_fact_command(command)
        for command in (
            *action.always,
            *action.on_success,
            *action.on_failure,
            *action.on_pushed_failure,
        )
    )
    if has_terminal_observation_fact and not is_source_terminal_observation_action(
        action,
        source_texts=source_texts,
    ):
        assumptions.append(
            "Unauthenticated terminal observation action was discarded: "
            f"{action.id}"
        )
        return None
    scene_conditions = tuple(
        condition
        for condition in canonical_conditions
        if condition.path == "scene_id"
    )
    if scene_conditions:
        if action.location_id is not None or action.global_action:
            assumptions.append(
                f"Action with conflicting location scopes was discarded: {action.id}"
            )
            return None
        scene_values = {
            condition.value
            for condition in scene_conditions
            if condition.operator == "eq"
            and isinstance(condition.value, str)
            and condition.value in location_ids
        }
        if len(scene_values) != 1 or any(
            condition.operator != "eq"
            or not isinstance(condition.value, str)
            or condition.value not in location_ids
            for condition in scene_conditions
        ):
            assumptions.append(
                f"Action with unknown or non-exact scene condition was discarded: {action.id}"
            )
            return None
        scene_value = next(iter(scene_values))
        if source_texts is not None and _unique_source_location_id(
            action,
            location_by_id,
            source_titles,
            source_section_paths,
            source_scene_keys,
            proven_initial_scene_id,
        ) != scene_value:
            assumptions.append(
                f"Action location was not anchored by its cited source and was discarded: {action.id}"
            )
            return None
        deduplicated: list[StateCondition] = []
        for condition in canonical_conditions:
            if condition.path != "scene_id" and condition not in deduplicated:
                deduplicated.append(condition)
        return action.model_copy(
            update={
                "preconditions": (
                    StateCondition(path="scene_id", operator="eq", value=scene_value),
                    *deduplicated,
                )
            }
        )

    if action.global_action:
        cited_texts = _action_source_texts(action, source_texts, source_titles)
        source_global = bool(cited_texts) and any(
            _source_supports_global_action(text) for text in cited_texts
        )
        if not source_global and not is_source_terminal_observation_action(
            action,
            source_texts=source_texts,
        ):
            assumptions.append(
                f"Source-unsupported global action was discarded: {action.id}"
            )
            return None
        return action.model_copy(update={"global_action": False})

    if action.location_id is None:
        if location_ids:
            matched_id = _unique_source_location_id(
                action,
                location_by_id,
                source_titles,
                source_section_paths,
                source_scene_keys,
                proven_initial_scene_id,
            )
            if matched_id is not None:
                location = location_by_id[matched_id]
                return action.model_copy(
                    update={
                        "preconditions": (
                            StateCondition(
                                path="scene_id", operator="eq", value=location.id
                            ),
                            *canonical_conditions,
                        )
                    }
                )
        if infer_unselected_location and location_ids:
            assumptions.append(
                "Coverage action without one uniquely source-anchored external "
                f"location was discarded: {action.id}"
            )
            return None
        if source_texts is None or not location_ids:
            return action
        assumptions.append(
            f"Evidence-backed action without a location scope was discarded: {action.id}"
        )
        return None
    location = location_by_id.get(action.location_id)
    if location is None:
        assumptions.append(f"Action with unknown location id was discarded: {action.id}")
        return None
    if source_texts is not None and _unique_source_location_id(
        action,
        location_by_id,
        source_titles,
        source_section_paths,
        source_scene_keys,
        proven_initial_scene_id,
    ) != location.id:
        assumptions.append(
            f"Action location was not anchored by its cited source and was discarded: {action.id}"
        )
        return None
    return action.model_copy(
        update={
            "location_id": None,
            "preconditions": (
                StateCondition(path="scene_id", operator="eq", value=location.id),
                *canonical_conditions,
            ),
        }
    )


def _unique_source_location_id(
    action: IrAction,
    location_by_id: Mapping[str, IrLocation | ActionScopeLocation],
    source_titles: dict[str, str] | None,
    source_section_paths: dict[str, tuple[str, ...]] | None,
    source_scene_keys: dict[str, str] | None,
    proven_initial_scene_id: str | None,
) -> str | None:
    source_aliases = {
        alias
        for source_id in action.source_block_ids
        for raw_alias in (
            source_titles.get(source_id, "") if source_titles else "",
            *(source_section_paths.get(source_id, ()) if source_section_paths else ()),
        )
        if (alias := canonical_location_alias(raw_alias))
    }
    matching_ids = _matching_location_ids(source_aliases, location_by_id)
    if len(matching_ids) == 1:
        return next(iter(matching_ids))
    scene_aliases = {
        alias
        for source_id in action.source_block_ids
        for raw_alias in (
            (source_scene_keys.get(source_id, "") if source_scene_keys else ""),
        )
        if (alias := canonical_location_alias(raw_alias))
    }
    scene_matches = _matching_location_ids(scene_aliases, location_by_id)
    if len(scene_matches) == 1:
        return next(iter(scene_matches))
    if proven_initial_scene_id in location_by_id and any(
        opening_scene_section_key(
            SceneGraphSourceContext(
                title=source_titles.get(source_id, "") if source_titles else "",
                section_path=(
                    source_section_paths.get(source_id, ())
                    if source_section_paths
                    else ()
                ),
            )
        )
        is not None
        for source_id in action.source_block_ids
    ):
        # The entry itself was independently source-proved before a later
        # coverage cycle. An Opening Scene mechanic therefore inherits that
        # exact scene; no other section gains this fallback.
        return proven_initial_scene_id
    return None


def _matching_location_ids(
    source_aliases: set[str],
    location_by_id: Mapping[str, IrLocation | ActionScopeLocation],
) -> set[str]:
    return {
        location.id
        for location in location_by_id.values()
        if source_aliases.intersection(
            {
                canonical_location_alias(location.id),
                canonical_location_alias(location.title),
            }
        )
    }


def canonical_location_alias(value: str) -> str:
    """Normalize formatting only; never infer semantic similarity."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith(("P", "Z"))
    )


def _action_source_texts(
    action: IrAction,
    source_texts: dict[str, str] | None,
    source_titles: dict[str, str] | None,
) -> tuple[str, ...]:
    if source_texts is None and source_titles is None:
        return ()
    return tuple(
        "\n".join(
            part
            for part in (
                source_titles.get(source_id, "") if source_titles else "",
                source_texts.get(source_id, "") if source_texts else "",
            )
            if part
        )
        for source_id in action.source_block_ids
        if (source_texts is not None and source_id in source_texts)
        or (source_titles is not None and source_id in source_titles)
    )


def _source_supports_global_action(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return any(
        marker in normalized
        for marker in (
            "任何地点",
            "任意地点",
            "无论在哪里",
            "无论身处何处",
            "无论调查员在何处",
            "随时随地",
            "跨场景",
            "全局行动",
            "any location",
            "from anywhere",
            "regardless of location",
            "from any scene",
            "global action",
            "system-wide",
        )
    )


__all__ = [
    "ActionScopeLocation",
    "bind_action_location_scope",
    "canonical_location_alias",
    "resolve_partition_action_location_slots",
]
