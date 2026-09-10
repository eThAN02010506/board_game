"""Materialize ordinary parent/child navigation from source section ancestry.

This is intentionally narrower than scene-graph authoring.  It recognizes only
existing locations whose titles (or ids) exactly match a structural section
component or its server-derived scene key.  Document order is never treated as
adjacency, and secret or conditional structure remains disconnected until a
separate authoritative operator unlocks it.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping

from ai_kp.platform.resolution.contracts import LocationLink, ScenarioContract, SourceRef
from ai_kp.platform.resolution.scenario_action_scope import canonical_location_alias

_SCENE_HEADING = re.compile(
    r"^(?:场景\s*(?:\d+|[0-9a-z一二三四五六七八九十百]+)|"
    r"scene\s*(?:\d+|[0-9a-z]+))"
    r"\s*[:\-]\s*.+$",
    re.IGNORECASE,
)
_SECRET_STRUCTURE = re.compile(
    r"(?:秘密|隐藏|隐秘|藏身处|墙后|暗门|密室|夹层|"
    r"\b(?:secret|hidden|concealed|behind\s+(?:the\s+)?wall|hideout|lair)\b)",
    re.IGNORECASE,
)
_CONDITIONAL_STRUCTURE = re.compile(
    r"(?:(?:如果|若(?:是)?|除非).{0,160}"
    r"(?:(?:暗门|门|墙|入口|通道).{0,40}(?:打开|解锁|打穿|破坏)|"
    r"(?:才能|方可|之后可|后可).{0,60}(?:进入|通往|抵达))|"
    r"(?:只能|仅能).{0,60}(?:打开|进入|通往)|"
    r"(?:门|入口|通道).{0,30}(?:上锁|锁住|锁着)|"
    r"\b(?:if|when|unless).{0,160}(?:unlock|open|break).{0,50}"
    r"(?:door|wall|entrance|passage)|\blocked\b)",
    re.IGNORECASE,
)
_ACCESS_RELATION = re.compile(
    r"(?:墙后|木板后|墙壁隔间|暗门|隐藏入口|秘密通道|密道|"
    r"打穿|解锁|发现.{0,40}(?:空洞|入口|通道)|"
    r"(?:空洞|入口|通道).{0,40}发现|"
    r"\b(?:behind\s+(?:the\s+)?wall|concealed\s+(?:door|entrance)|"
    r"secret\s+(?:door|entrance|passage)|break\s+through|unlock|"
    r"discover.{0,40}(?:opening|entrance|passage))\b)",
    re.IGNORECASE,
)
_ROOM_NUMBER = re.compile(
    r"^(?:(?:第\s*)?(?P<before>\d+|[一二三四五六七八九十百]+)\s*"
    r"号\s*房(?:间)?|房间\s*(?P<after>\d+|[一二三四五六七八九十百]+))",
    re.IGNORECASE,
)


def _location_aliases(location_id: str, title: str) -> frozenset[str]:
    return frozenset(
        alias
        for value in (location_id, title)
        if (alias := canonical_location_alias(value))
    )


def _component_aliases(
    section_path: tuple[str, ...], scene_key: str | None
) -> tuple[frozenset[str], ...]:
    aliases = [
        {alias}
        if (alias := canonical_location_alias(component))
        else set()
        for component in section_path
    ]
    scene_alias = canonical_location_alias(scene_key or "")
    if scene_alias:
        scene_indexes = tuple(
            index
            for index, component in enumerate(section_path)
            if _SCENE_HEADING.fullmatch(
                unicodedata.normalize("NFKC", component.strip())
            )
        )
        if len(scene_indexes) == 1:
            aliases[scene_indexes[0]].add(scene_alias)
    return tuple(frozenset(item) for item in aliases)


def _source_child_aliases(title: str) -> frozenset[str]:
    aliases = {canonical_location_alias(title)}
    normalized = unicodedata.normalize("NFKC", " ".join(title.split()))
    match = _ROOM_NUMBER.match(normalized)
    if match is not None:
        number = canonical_location_alias(
            match.group("before") or match.group("after") or ""
        )
        aliases.update((f"{number}号房间", f"房间{number}", f"{number}号"))
    return frozenset(alias for alias in aliases if alias)


def _text_asserts_bounded_access_relation(
    text: str,
    *,
    child_aliases: frozenset[str],
    source_is_child_context: bool,
) -> bool:
    for sentence in re.split(r"(?<=[。！？.!?])\s*|[\r\n]+", text):
        if not sentence or not (
            _CONDITIONAL_STRUCTURE.search(sentence)
            or _ACCESS_RELATION.search(sentence)
        ):
            continue
        if source_is_child_context:
            return True
        canonical = canonical_location_alias(sentence)
        for alias in child_aliases:
            if alias not in canonical:
                continue
            if alias.endswith("号") and "房间" not in canonical:
                continue
            return True
    return False


def _path_starts_with(
    path: tuple[str, ...], prefix: tuple[str, ...]
) -> bool:
    return len(path) >= len(prefix) and path[: len(prefix)] == prefix


def materialize_source_structure_navigation(
    contract: ScenarioContract,
    *,
    source_texts: Mapping[str, str],
    source_section_paths: Mapping[str, tuple[str, ...]],
    source_scene_keys: Mapping[str, str] | None = None,
) -> ScenarioContract:
    """Add bidirectional links for unambiguous, ordinary section ancestry.

    A child may cite multiple source blocks.  They must all resolve to the same
    parent; conflicting parents fail closed.  Existing links are never widened
    or replaced, which keeps this transformation idempotent and compatible with
    externally supplied or incrementally authored locations.
    """

    if len(contract.locations) < 2:
        return contract

    aliases_by_id = {
        location.location_id: _location_aliases(
            location.location_id, location.title
        )
        for location in contract.locations
    }
    owners: dict[str, set[str]] = defaultdict(set)
    for location_id, aliases in aliases_by_id.items():
        for alias in aliases:
            owners[alias].add(location_id)

    proposed: dict[str, tuple[str, list[SourceRef]]] = {}
    conflicted_children: set[str] = set()
    unsafe_children: set[str] = set()
    child_contexts: dict[str, set[tuple[tuple[str, ...], str]]] = defaultdict(set)
    for child in contract.locations:
        child_aliases = aliases_by_id[child.location_id]
        for ref in child.source_refs:
            source_id = ref.source_block_id
            section_path = source_section_paths.get(source_id)
            source_text = source_texts.get(source_id)
            if not section_path or source_text is None:
                continue
            component_aliases = _component_aliases(
                section_path,
                (source_scene_keys or {}).get(source_id),
            )
            child_indexes = tuple(
                index
                for index, aliases in enumerate(component_aliases)
                if aliases.intersection(child_aliases)
            )
            if not child_indexes:
                continue
            child_index = child_indexes[-1]
            child_contexts[child.location_id].add(
                (section_path[:child_index], child.title)
            )

            parent_id: str | None = None
            parent_index: int | None = None
            ambiguous_parent = False
            for index in range(child_index - 1, -1, -1):
                candidates = {
                    location_id
                    for alias in component_aliases[index]
                    for location_id in owners.get(alias, ())
                    if location_id != child.location_id
                }
                if len(candidates) > 1:
                    ambiguous_parent = True
                    break
                if len(candidates) == 1:
                    parent_id = next(iter(candidates))
                    parent_index = index
                    break
            if ambiguous_parent or parent_id is None or parent_index is None:
                if ambiguous_parent:
                    conflicted_children.add(child.location_id)
                continue
            structural_parts = section_path[parent_index + 1 : child_index + 1]
            if _SECRET_STRUCTURE.search("\n".join(structural_parts)) or (
                _CONDITIONAL_STRUCTURE.search("\n".join(structural_parts))
            ) or _text_asserts_bounded_access_relation(
                source_text,
                child_aliases=_source_child_aliases(child.title),
                source_is_child_context=True,
            ):
                unsafe_children.add(child.location_id)
                continue

            previous = proposed.get(child.location_id)
            if previous is None:
                proposed[child.location_id] = (parent_id, [ref])
            elif previous[0] != parent_id:
                conflicted_children.add(child.location_id)
            elif ref not in previous[1]:
                previous[1].append(ref)

    # Access evidence can live under a sibling section (for example, a visible
    # room describes the wall that must be breached to reach another room).
    # Search only the same exact structural container and require the sentence
    # to name the child by its full title or explicit numbered-room locator.
    for child_id, contexts in child_contexts.items():
        if child_id in unsafe_children:
            continue
        for container, title in contexts:
            aliases = _source_child_aliases(title)
            if any(
                _path_starts_with(source_section_paths.get(source_id, ()), container)
                and _text_asserts_bounded_access_relation(
                    text,
                    child_aliases=aliases,
                    source_is_child_context=False,
                )
                for source_id, text in source_texts.items()
            ):
                unsafe_children.add(child_id)
                break

    links = list(contract.location_links)
    connected_pairs = {
        frozenset((link.from_location_id, link.to_location_id)) for link in links
    }
    for child in contract.locations:
        if child.location_id in conflicted_children or child.location_id in unsafe_children:
            continue
        resolved = proposed.get(child.location_id)
        if resolved is None:
            continue
        parent_id, refs = resolved
        pair = frozenset((parent_id, child.location_id))
        if len(pair) != 2 or pair in connected_pairs:
            continue
        links.append(
            LocationLink(
                from_location_id=parent_id,
                to_location_id=child.location_id,
                one_way=False,
                source_refs=tuple(refs),
            )
        )
        connected_pairs.add(pair)

    if len(links) == len(contract.location_links):
        return contract
    return contract.model_copy(update={"location_links": tuple(links)})


__all__ = ["materialize_source_structure_navigation"]
