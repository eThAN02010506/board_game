"""Deterministic, source-ancestry-bounded Scenario IR location identity."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from ai_kp.platform.modules.section_ancestry import inferred_scene_key
from ai_kp.platform.resolution.contracts import (
    ConsequenceSignalBand,
    PressureStage,
    ReactiveRule,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.location_references import is_location_condition_path
from ai_kp.platform.resolution.scenario_action_scope import canonical_location_alias
from ai_kp.platform.resolution.scenario_ir_models import (
    IrAction,
    IrClock,
    IrConsequenceSignal,
    IrEnding,
    IrEntity,
    IrLocation,
    IrLocationLink,
    IrReactivePolicy,
    IrResponseObligation,
    IrTaskMethod,
    ScenarioIrBatch,
)

_ROOM_PREFIX = re.compile(
    r"^(?:第\s*)?(?P<number>\d+|[一二三四五六七八九十百]+)\s*"
    r"号\s*房(?:间)?\s*(?:[:\-—]\s*)?(?P<title>.*)$",
    re.IGNORECASE,
)
_REVERSED_ROOM_PREFIX = re.compile(
    r"^房间\s*(?P<number>\d+|[一二三四五六七八九十百]+)\s*"
    r"(?:[:\-—]\s*)?(?P<title>.*)$",
    re.IGNORECASE,
)
_VISIBILITY_RANK = {"hidden": 0, "known": 1, "visited": 2}
_ROOT_SCENE_COMPONENT_SEPARATOR = re.compile(r"[;；/]")
_NON_PLAYABLE_LOCATION_LABEL = re.compile(
    r"^(?:"
    r"(?:文字|展示|玩家|调查员)\s*材料\s*\d*"
    r"|handout\s*(?:no\.?\s*)?\d*"
    r"|player\s+handout\s*\d*"
    r"|(?:mp|hp|san|db|build)\s*[:：]\s*[-+\d][^\n]{0,100}"
    r"|扮演须知|守秘人(?:须知|提示|说明)|主持人(?:须知|提示|说明)"
    r"|(?:keeper|gm|game\s+master|roleplaying|playing)\s+"
    r"(?:notes?|guidance|tips?)"
    r")\s*[:：]?\s*(?:\([^)]*\))?$",
    re.IGNORECASE,
)
_PLACE_ASSERTION = re.compile(
    r"(?:位于|坐落(?:于)?|住在|进入|前往|抵达|来到|地址|"
    r"房屋|房子|建筑|房间|场所|地点|城市|村庄|街道|道路|"
    r"车站|车厢|图书馆|报社|警察局|法院|医院|学校|旅馆|教堂|地下室|楼层|"
    r"\b(?:located|situated|address|enter|entered|visit|travel|go|went|arrive|"
    r"inside|building|house|room|place|location|city|village|street|road|"
    r"station|carriage|library|office|court|hospital|school|hotel|church|"
    r"basement|floor)\b)",
    re.IGNORECASE,
)
_CLAUSE_SPLIT = re.compile(r"[;；。！？!?]+|\n+")
_NON_SPATIAL_SOURCE_KINDS = frozenset(
    {
        "scenario_metadata",
        "keeper_overview",
        "stat_block",
        "handout",
        "reference",
        "table",
    }
)


@dataclass(frozen=True)
class _LocationOccurrence:
    batch_index: int
    location_index: int
    location: IrLocation
    identity: str
    parent: tuple[str, ...] | None
    root_scene_component: bool = False
    conflict_reason: str | None = None


def reconcile_location_identities(
    batches: tuple[ScenarioIrBatch, ...],
    *,
    source_section_paths: Mapping[str, tuple[str, ...]] | None,
    source_texts: Mapping[str, str] | None = None,
    source_titles: Mapping[str, str] | None = None,
    source_scene_keys: Mapping[str, str] | None = None,
    source_semantic_kinds: Mapping[str, str] | None = None,
    protected_location_ids: frozenset[str] = frozenset(),
    assumptions: list[str],
) -> tuple[ScenarioIrBatch, ...]:
    """Merge format-equivalent locations and rewrite every explicit IR reference."""

    occurrences = tuple(
        _location_occurrence(
            batch_index,
            location_index,
            location,
            source_section_paths or {},
        )
        for batch_index, batch in enumerate(batches)
        for location_index, location in enumerate(batch.locations)
    )
    if not occurrences:
        return batches

    role_rejected_positions = {
        (occurrence.batch_index, occurrence.location_index)
        for occurrence in occurrences
        if occurrence.location.id not in protected_location_ids
        and not source_supports_playable_location(
            occurrence.location.title,
            occurrence.location.source_block_ids,
            source_texts=source_texts,
            source_titles=source_titles,
            source_section_paths=source_section_paths,
            source_scene_keys=source_scene_keys,
            source_semantic_kinds=source_semantic_kinds,
        )
    }
    for occurrence in occurrences:
        if (occurrence.batch_index, occurrence.location_index) in (
            role_rejected_positions
        ):
            assumptions.append(
                "Source did not prove a playable location role; IR location was "
                f"discarded: {occurrence.location.id} ({occurrence.location.title})"
            )
        if occurrence.conflict_reason:
            assumptions.append(occurrence.conflict_reason)

    occurrences_by_id: dict[str, list[_LocationOccurrence]] = defaultdict(list)
    for occurrence in occurrences:
        if (occurrence.batch_index, occurrence.location_index) in role_rejected_positions:
            continue
        occurrences_by_id[occurrence.location.id].append(occurrence)
    rejected_positions: set[tuple[int, int]] = set()
    conflicting_ids: set[str] = set()
    for location_id, members in occurrences_by_id.items():
        identities = {(item.identity, item.parent) for item in members}
        if len(identities) <= 1:
            continue
        aligned_identities = {
            (item.identity, item.parent)
            for item in members
            if _source_identity_aligned(item, source_section_paths or {})
        }
        if len(aligned_identities) != 1:
            conflicting_ids.add(location_id)
            continue
        retained_identity = next(iter(aligned_identities))
        rejected_positions.update(
            (item.batch_index, item.location_index)
            for item in members
            if (item.identity, item.parent) != retained_identity
        )
        assumptions.append(
            "Conflicting location occurrences outside the unique source identity "
            f"were discarded: {location_id}"
        )
    conflicting_ids |= {
        occurrence.location.id
        for occurrence in occurrences
        if occurrence.conflict_reason is not None
    }
    if conflicting_ids:
        assumptions.append(
            "Location ids with conflicting source identities were discarded: "
            + ", ".join(sorted(conflicting_ids))
        )

    valid = tuple(
        occurrence
        for occurrence in occurrences
        if occurrence.location.id not in conflicting_ids
        and (occurrence.batch_index, occurrence.location_index)
        not in role_rejected_positions
        and (occurrence.batch_index, occurrence.location_index)
        not in rejected_positions
    )
    identity_parents: dict[str, set[tuple[str, ...] | None]] = defaultdict(set)
    for occurrence in valid:
        identity_parents[occurrence.identity].add(occurrence.parent)
    for identity, parents in sorted(identity_parents.items()):
        known = {parent for parent in parents if parent is not None}
        if len(known) > 1:
            ids = sorted(
                {
                    occurrence.location.id
                    for occurrence in valid
                    if occurrence.identity == identity
                }
            )
            assumptions.append(
                "Format-equivalent locations were kept separate across source parents: "
                + ", ".join(ids)
            )

    groups: dict[tuple[str, tuple[str, ...] | None], list[_LocationOccurrence]] = (
        defaultdict(list)
    )
    for occurrence in valid:
        groups[(occurrence.identity, occurrence.parent)].append(occurrence)

    replacements: dict[str, str] = {}
    merged_locations: dict[tuple[str, tuple[str, ...] | None], IrLocation] = {}
    first_occurrence: dict[tuple[str, tuple[str, ...] | None], tuple[int, int]] = {}
    for group_key, members in groups.items():
        exact_identity_members = (
            tuple(
                member
                for member in members
                if _title_identity(member.location.title) == group_key[0]
            )
            if any(member.root_scene_component for member in members)
            else ()
        )
        canonical_id = min(
            member.location.id for member in (exact_identity_members or members)
        )
        for member in members:
            replacements[member.location.id] = canonical_id
        merged_locations[group_key] = _merge_locations(
            members,
            canonical_id,
            identity=group_key[0],
        )
        first_occurrence[group_key] = min(
            (member.batch_index, member.location_index) for member in members
        )
        distinct_ids = sorted({member.location.id for member in members})
        if len(distinct_ids) > 1:
            assumptions.append(
                f"Format-equivalent locations merged as {canonical_id}: "
                + ", ".join(distinct_ids)
            )

    occurrence_by_position = {
        (item.batch_index, item.location_index): item for item in valid
    }
    reconciled: list[ScenarioIrBatch] = []
    for batch_index, batch in enumerate(batches):
        locations: list[IrLocation] = []
        for location_index, _location in enumerate(batch.locations):
            occurrence = occurrence_by_position.get((batch_index, location_index))
            if occurrence is None:
                continue
            group_key = (occurrence.identity, occurrence.parent)
            if first_occurrence[group_key] == (batch_index, location_index):
                locations.append(merged_locations[group_key])
        reconciled.append(
            _rewrite_batch(
                batch,
                locations=tuple(locations),
                replacements=replacements,
                discarded_ids={
                    *conflicting_ids,
                    *(
                        item.location.id
                        for item in occurrences
                        if item.batch_index == batch_index
                        and (
                            (item.batch_index, item.location_index)
                            in rejected_positions
                            or (item.batch_index, item.location_index)
                            in role_rejected_positions
                        )
                    ),
                },
                assumptions=assumptions,
            )
        )
    return tuple(reconciled)


def source_supports_playable_location(
    title: str,
    source_block_ids: tuple[str, ...],
    *,
    source_texts: Mapping[str, str] | None,
    source_titles: Mapping[str, str] | None,
    source_section_paths: Mapping[str, tuple[str, ...]] | None,
    source_scene_keys: Mapping[str, str] | None,
    source_semantic_kinds: Mapping[str, str] | None,
) -> bool:
    """Require structural or explicit prose evidence for an executable place.

    MinerU/Docling-style layout labels distinguish titles and text, not TRPG
    ontology.  A model may therefore propose a location, but it cannot turn a
    handout label, statistic, or keeper instruction into a scene merely because
    that text occupies a heading box.
    """

    if source_semantic_kinds is None:
        # Programmatic/manual assembly without document-role metadata retains
        # its historical behavior. Evidence-backed AI authoring always passes
        # this catalog.
        return True
    normalized_title = " ".join(title.split()).strip()
    if not normalized_title or _NON_PLAYABLE_LOCATION_LABEL.fullmatch(
        normalized_title
    ):
        return False
    title_alias = canonical_location_alias(normalized_title)
    if not title_alias:
        return False
    for source_id in source_block_ids:
        semantic_kind = source_semantic_kinds.get(source_id, "text")
        source_title = (source_titles or {}).get(source_id, "")
        section_path = (source_section_paths or {}).get(source_id, ())
        scene_key = (source_scene_keys or {}).get(source_id, "")
        structural_values = (source_title, *section_path, scene_key)
        structural_match = any(
            canonical_location_alias(value) == title_alias
            for value in structural_values
            if value
        )
        scene_match = bool(
            scene_key and canonical_location_alias(scene_key) == title_alias
        )
        if scene_match or (
            structural_match and semantic_kind not in _NON_SPATIAL_SOURCE_KINDS
        ):
            return True
        if semantic_kind in _NON_SPATIAL_SOURCE_KINDS:
            continue
        for clause in _CLAUSE_SPLIT.split((source_texts or {}).get(source_id, "")):
            if (
                title_alias in canonical_location_alias(clause)
                and _PLACE_ASSERTION.search(clause)
            ):
                return True
    return False


def _location_occurrence(
    batch_index: int,
    location_index: int,
    location: IrLocation,
    source_section_paths: Mapping[str, tuple[str, ...]],
) -> _LocationOccurrence:
    root_scene_identity = _root_scene_container_identity(
        location,
        source_section_paths,
    )
    title_identity = root_scene_identity or _title_identity(location.title)
    inferred_room_identities = {
        identity
        for source_id in location.source_block_ids
        for component in source_section_paths.get(source_id, ())
        if (identity := _room_identity(component)) is not None
        and _room_suffix(identity) == canonical_location_alias(location.title)
    }
    identity_conflict = len(inferred_room_identities) > 1
    identity = (
        f"!conflict:{batch_index}:{location_index}:{location.id}"
        if identity_conflict
        else (
            next(iter(inferred_room_identities))
            if inferred_room_identities
            else title_identity
        )
    )
    parents = {
        parent
        for source_id in location.source_block_ids
        if (
            parent := _source_parent(
                source_section_paths.get(source_id, ()), identity, location.title
            )
        )
        is not None
    }
    parent_conflict = len(parents) > 1
    parent = (
        (f"!conflict:{batch_index}:{location_index}:{location.id}",)
        if parent_conflict
        else (next(iter(parents)) if parents else None)
    )
    conflict_reason = None
    if identity_conflict:
        conflict_reason = (
            "Location with conflicting numbered-room ancestry was isolated: "
            f"{location.id}"
        )
    elif parent_conflict:
        conflict_reason = (
            "Location with conflicting source parents was isolated: " f"{location.id}"
        )
    return _LocationOccurrence(
        batch_index=batch_index,
        location_index=location_index,
        location=location,
        identity=identity,
        parent=parent,
        root_scene_component=root_scene_identity is not None,
        conflict_reason=conflict_reason,
    )


def _root_scene_container_identity(
    location: IrLocation,
    source_section_paths: Mapping[str, tuple[str, ...]],
) -> str | None:
    """Treat model-split components of one root scene heading as that scene."""

    location_alias = canonical_location_alias(location.title)
    identities: set[str] = set()
    for source_id in location.source_block_ids:
        section_path = source_section_paths.get(source_id, ())
        if len(section_path) != 1:
            continue
        scene_key = inferred_scene_key(section_path)
        if scene_key is None:
            continue
        components = tuple(
            canonical_location_alias(item)
            for item in _ROOT_SCENE_COMPONENT_SEPARATOR.split(scene_key)
            if canonical_location_alias(item)
        )
        scene_alias = canonical_location_alias(scene_key)
        heading_alias = canonical_location_alias(section_path[0])
        if scene_alias and (
            location_alias in {heading_alias, scene_alias}
            or (len(components) >= 2 and location_alias in components)
        ):
            identities.add(f"title:{scene_alias}")
    return next(iter(identities)) if len(identities) == 1 else None


def _title_identity(title: str) -> str:
    return _room_identity(title) or f"title:{canonical_location_alias(title)}"


def _source_identity_aligned(
    occurrence: _LocationOccurrence,
    source_section_paths: Mapping[str, tuple[str, ...]],
) -> bool:
    title_alias = canonical_location_alias(occurrence.location.title)
    for source_id in occurrence.location.source_block_ids:
        section_path = source_section_paths.get(source_id, ())
        if any(
            canonical_location_alias(component) == title_alias
            for component in section_path
        ):
            return True
        scene_key = inferred_scene_key(section_path)
        if scene_key and canonical_location_alias(scene_key) == title_alias:
            return True
    return False


def _room_identity(title: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", " ".join(title.split())).casefold()
    normalized = normalized.replace("：", ":")
    match = _ROOM_PREFIX.fullmatch(normalized) or _REVERSED_ROOM_PREFIX.fullmatch(
        normalized
    )
    if match is None:
        return None
    suffix = canonical_location_alias(match.group("title"))
    number = canonical_location_alias(match.group("number"))
    return f"room:{number}:{suffix}" if suffix else f"room:{number}"


def _room_suffix(identity: str) -> str:
    return identity.split(":", 2)[2] if identity.count(":") >= 2 else ""


def _source_parent(
    section_path: tuple[str, ...], identity: str, title: str
) -> tuple[str, ...] | None:
    title_alias = canonical_location_alias(title)
    for index in range(len(section_path) - 1, -1, -1):
        component = section_path[index]
        if _title_identity(component) == identity or canonical_location_alias(
            component
        ) == title_alias:
            parent = tuple(
                alias
                for item in section_path[:index]
                if (alias := canonical_location_alias(item))
            )
            return parent or None
    return None


def _merge_locations(
    members: list[_LocationOccurrence],
    canonical_id: str,
    *,
    identity: str,
) -> IrLocation:
    locations = [member.location for member in members]
    titled = min(
        locations,
        key=lambda item: (
            _title_identity(item.title) != identity,
            _room_identity(item.title) is None,
            canonical_location_alias(item.title),
            unicodedata.normalize("NFKC", item.title).casefold(),
            item.id,
        ),
    )
    visibility = max(
        (item.visibility for item in locations), key=_VISIBILITY_RANK.__getitem__
    )
    return titled.model_copy(
        update={
            "id": canonical_id,
            "visibility": visibility,
            "tags": tuple(sorted({tag for item in locations for tag in item.tags})),
            "source_block_ids": tuple(
                sorted(
                    {
                        source_id
                        for item in locations
                        for source_id in item.source_block_ids
                    }
                )
            ),
        }
    )


def _rewrite_batch(
    batch: ScenarioIrBatch,
    *,
    locations: tuple[IrLocation, ...],
    replacements: Mapping[str, str],
    discarded_ids: set[str],
    assumptions: list[str],
) -> ScenarioIrBatch:
    links: list[IrLocationLink] = []
    for link in batch.location_links:
        if link.from_id in discarded_ids or link.to_id in discarded_ids:
            assumptions.append(
                f"Location link with a conflicting identity was discarded: "
                f"{link.from_id} -> {link.to_id}"
            )
            continue
        rewritten = link.model_copy(
            update={
                "from_id": replacements.get(link.from_id, link.from_id),
                "to_id": replacements.get(link.to_id, link.to_id),
                "preconditions": _conditions(link.preconditions, replacements),
            }
        )
        if rewritten.from_id == rewritten.to_id:
            assumptions.append(
                f"Location link collapsed to itself and was discarded: {link.from_id}"
            )
            continue
        links.append(rewritten)
    return batch.model_copy(
        update={
            "initial_scene_id": _reference(
                batch.initial_scene_id, replacements, discarded_ids
            ),
            "locations": locations,
            "location_links": tuple(links),
            "entities": tuple(
                _entity(item, replacements, discarded_ids) for item in batch.entities
            ),
            "clocks": tuple(_clock(item, replacements) for item in batch.clocks),
            "actions": tuple(
                _action(item, replacements, discarded_ids) for item in batch.actions
            ),
            "task_methods": tuple(
                _method(item, replacements) for item in batch.task_methods
            ),
            "reactive_policies": tuple(
                _policy(item, replacements) for item in batch.reactive_policies
            ),
            "consequence_signals": tuple(
                _signal(item, replacements) for item in batch.consequence_signals
            ),
            "endings": tuple(_ending(item, replacements) for item in batch.endings),
        }
    )


def _reference(
    value: str | None, replacements: Mapping[str, str], discarded_ids: set[str]
) -> str | None:
    if value is None or value in discarded_ids:
        return None
    return replacements.get(value, value)


def _condition(
    condition: StateCondition, replacements: Mapping[str, str]
) -> StateCondition:
    path = condition.path.replace("[", ".").replace("]", "").strip(".")
    location_path = is_location_condition_path(path)
    if (
        location_path
        and isinstance(condition.value, str)
        and condition.value in replacements
    ):
        return condition.model_copy(update={"value": replacements[condition.value]})
    return condition


def _conditions(
    conditions: tuple[StateCondition, ...], replacements: Mapping[str, str]
) -> tuple[StateCondition, ...]:
    return tuple(_condition(item, replacements) for item in conditions)


def _command(command: WorldCommand, replacements: Mapping[str, str]) -> WorldCommand:
    if (
        command.kind in {"set_scene", "move_actor"}
        and isinstance(command.value, str)
        and command.value in replacements
    ):
        return command.model_copy(update={"value": replacements[command.value]})
    return command


def _commands(
    commands: tuple[WorldCommand, ...], replacements: Mapping[str, str]
) -> tuple[WorldCommand, ...]:
    return tuple(_command(item, replacements) for item in commands)


def _obligation(
    obligation: IrResponseObligation, replacements: Mapping[str, str]
) -> IrResponseObligation:
    return obligation.model_copy(
        update={"conditions": _conditions(obligation.conditions, replacements)}
    )


def _entity(
    entity: IrEntity,
    replacements: Mapping[str, str],
    discarded_ids: set[str],
) -> IrEntity:
    return entity.model_copy(
        update={
            "location_id": _reference(entity.location_id, replacements, discarded_ids),
            "response_obligations": tuple(
                _obligation(item, replacements) for item in entity.response_obligations
            ),
        }
    )


def _stage(stage: PressureStage, replacements: Mapping[str, str]) -> PressureStage:
    return stage.model_copy(update={"commands": _commands(stage.commands, replacements)})


def _clock(clock: IrClock, replacements: Mapping[str, str]) -> IrClock:
    return clock.model_copy(
        update={
            "pressure_stages": tuple(
                _stage(item, replacements) for item in clock.pressure_stages
            )
        }
    )


def _action(
    action: IrAction,
    replacements: Mapping[str, str],
    discarded_ids: set[str],
) -> IrAction:
    return action.model_copy(
        update={
            "location_id": _reference(action.location_id, replacements, discarded_ids),
            "preconditions": _conditions(action.preconditions, replacements),
            "always": _commands(action.always, replacements),
            "on_success": _commands(action.on_success, replacements),
            "on_failure": _commands(action.on_failure, replacements),
            "on_pushed_failure": _commands(action.on_pushed_failure, replacements),
        }
    )


def _method(method: IrTaskMethod, replacements: Mapping[str, str]) -> IrTaskMethod:
    return method.model_copy(
        update={"preconditions": _conditions(method.preconditions, replacements)}
    )


def _rule(rule: ReactiveRule, replacements: Mapping[str, str]) -> ReactiveRule:
    return rule.model_copy(
        update={
            "conditions": _conditions(rule.conditions, replacements),
            "commands": _commands(rule.commands, replacements),
        }
    )


def _policy(
    policy: IrReactivePolicy, replacements: Mapping[str, str]
) -> IrReactivePolicy:
    return policy.model_copy(
        update={"rules": tuple(_rule(item, replacements) for item in policy.rules)}
    )


def _band(
    band: ConsequenceSignalBand, replacements: Mapping[str, str]
) -> ConsequenceSignalBand:
    return band.model_copy(
        update={"all_conditions": _conditions(band.all_conditions, replacements)}
    )


def _signal(
    signal: IrConsequenceSignal, replacements: Mapping[str, str]
) -> IrConsequenceSignal:
    return signal.model_copy(
        update={"bands": tuple(_band(item, replacements) for item in signal.bands)}
    )


def _ending(ending: IrEnding, replacements: Mapping[str, str]) -> IrEnding:
    return ending.model_copy(
        update={
            "all_conditions": _conditions(ending.all_conditions, replacements),
            "any_conditions": _conditions(ending.any_conditions, replacements),
            "commands": _commands(ending.commands, replacements),
        }
    )


__all__ = ["reconcile_location_identities", "source_supports_playable_location"]
