"""Exact source-location authority shared by scene transition materializers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from ai_kp.platform.modules.section_ancestry import inferred_scene_key
from ai_kp.platform.resolution.contracts import LocationSpec, ScenarioContract
from ai_kp.platform.resolution.scenario_action_scope import canonical_location_alias

_ROOM_PREFIX = re.compile(
    r"^(?:第\s*)?(?:(?P<number_a>\d+|[一二三四五六七八九十百]+)\s*"
    r"号\s*房(?:间)?|房间\s*(?P<number_b>\d+|[一二三四五六七八九十百]+)|"
    r"room\s*(?P<number_c>\d+))",
    re.IGNORECASE,
)
_ROOM_MENTION = re.compile(_ROOM_PREFIX.pattern.removeprefix("^"), re.IGNORECASE)
_COORDINATED_ROOM_MENTION = re.compile(
    r"(?P<first>\d+|[一二三四五六七八九十百]+)\s*号\s*"
    r"(?:和|及|与|、|,)\s*"
    r"(?P<second>\d+|[一二三四五六七八九十百]+)\s*号\s*房(?:间)?|"
    r"\brooms?\s*(?P<english_first>\d+)\s*(?:and|&)\s*"
    r"(?P<english_second>\d+)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceLocationAuthority:
    location: LocationSpec
    aliases: frozenset[str]
    parent_paths: frozenset[tuple[str, ...]]
    scene_keys: frozenset[str]


def _room_alias(value: str) -> str | None:
    matched = _ROOM_PREFIX.match(value.strip())
    if matched is None:
        return None
    number = (
        matched.group("number_a")
        or matched.group("number_b")
        or matched.group("number_c")
    )
    return f"房间{canonical_location_alias(number)}"


def source_location_catalog(
    contract: ScenarioContract,
    *,
    source_section_paths: Mapping[str, tuple[str, ...]],
    source_scene_keys: Mapping[str, str] | None,
) -> tuple[SourceLocationAuthority, ...]:
    catalog: list[SourceLocationAuthority] = []
    for location in contract.locations:
        aliases = {canonical_location_alias(location.title)}
        room = _room_alias(location.title)
        if room:
            aliases.add(room)
        parents: set[tuple[str, ...]] = set()
        scene_keys: set[str] = set()
        for ref in location.source_refs:
            path = source_section_paths.get(ref.source_block_id, ())
            scene = (source_scene_keys or {}).get(
                ref.source_block_id
            ) or inferred_scene_key(path)
            scene_alias = canonical_location_alias(scene or "")
            if scene_alias:
                scene_keys.add(scene_alias)
                title_alias = canonical_location_alias(location.title)
                if title_alias.startswith(scene_alias) and title_alias != scene_alias:
                    aliases.add(title_alias.removeprefix(scene_alias))
            for index in range(len(path) - 1, -1, -1):
                component = path[index]
                if (
                    canonical_location_alias(component) in aliases
                    or _room_alias(component) in aliases
                ):
                    parents.add(
                        tuple(
                            canonical_location_alias(item)
                            for item in path[:index]
                            if canonical_location_alias(item)
                        )
                    )
                    break
        catalog.append(
            SourceLocationAuthority(
                location=location,
                aliases=frozenset(item for item in aliases if item),
                parent_paths=frozenset(parents),
                scene_keys=frozenset(scene_keys),
            )
        )
    return tuple(catalog)


def mentioned_source_locations(
    text: str,
    catalog: tuple[SourceLocationAuthority, ...],
) -> tuple[SourceLocationAuthority, ...]:
    canonical = canonical_location_alias(text)
    room_mentions = {
        "房间"
        + canonical_location_alias(
            matched.group("number_a")
            or matched.group("number_b")
            or matched.group("number_c")
            or ""
        )
        for matched in _ROOM_MENTION.finditer(text)
    }
    for matched in _COORDINATED_ROOM_MENTION.finditer(text):
        for number in (
            matched.group("first") or matched.group("english_first"),
            matched.group("second") or matched.group("english_second"),
        ):
            room_mentions.add(f"房间{canonical_location_alias(number or '')}")
    return tuple(
        item
        for item in catalog
        if any(alias and alias in canonical for alias in item.aliases)
        or bool(room_mentions.intersection(item.aliases))
    )


def source_location_from_ancestry(
    section_path: tuple[str, ...],
    catalog: tuple[SourceLocationAuthority, ...],
) -> SourceLocationAuthority | None:
    for index in range(len(section_path) - 1, -1, -1):
        matches = mentioned_source_locations(section_path[index], catalog)
        if len(matches) > 1:
            parent_path = tuple(
                canonical_location_alias(item)
                for item in section_path[:index]
                if canonical_location_alias(item)
            )
            matches = tuple(
                item for item in matches if parent_path in item.parent_paths
            )
            if len(matches) > 1:
                return None
        if len(matches) == 1:
            return matches[0]
    return None


def same_immediate_source_container(
    origin: SourceLocationAuthority,
    destination: SourceLocationAuthority,
) -> bool:
    return bool(origin.parent_paths.intersection(destination.parent_paths))


def same_source_scene_container(
    origin_id: str,
    destination: SourceLocationAuthority,
    catalog: tuple[SourceLocationAuthority, ...],
    evidence_path: tuple[str, ...],
) -> bool:
    origin = next(
        (item for item in catalog if item.location.location_id == origin_id), None
    )
    evidence_scene = canonical_location_alias(inferred_scene_key(evidence_path) or "")
    if origin is None:
        return False
    shared = origin.scene_keys.intersection(destination.scene_keys)
    return bool(shared and (not evidence_scene or evidence_scene in shared))


__all__ = [
    "SourceLocationAuthority",
    "mentioned_source_locations",
    "same_immediate_source_container",
    "same_source_scene_container",
    "source_location_catalog",
    "source_location_from_ancestry",
]
