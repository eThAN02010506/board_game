"""Bounded scene-graph repair over source-grounded authored locations.

The model-facing envelope can select existing location slots and evidence blocks,
but it cannot invent location identities, visibility, conditions, or world effects.
The strict IR remains the only representation that crosses into compilation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_kp.platform.resolution.contracts import (
    LocationLink,
    LocationSpec,
    ScenarioContract,
    SourceRef,
)


class SceneGraphLinkSelection(BaseModel):
    """One source-asserted route between two server-enumerated locations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_slot: int = Field(ge=0, le=255)
    to_slot: int = Field(ge=0, le=255)
    one_way: bool = False
    source_block_ids: tuple[str, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def reject_self_link(self) -> SceneGraphLinkSelection:
        if self.from_slot == self.to_slot:
            raise ValueError("A scene route must connect two different locations")
        return self


class SceneGraphLocationCandidate(BaseModel):
    """Read-only, opaque-slot catalog item shown to the topology agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: int = Field(ge=0, le=255)
    title: str = Field(min_length=1, max_length=240)
    source_block_ids: tuple[str, ...] = Field(min_length=1, max_length=16)


class SceneGraphSupplementEnvelope(BaseModel):
    """Narrow model response: selections only, never authored records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_scene_slot: int | None = Field(default=None, ge=0, le=255)
    initial_scene_source_block_ids: tuple[str, ...] = Field(default=(), max_length=8)
    links: tuple[SceneGraphLinkSelection, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def require_initial_scene_evidence(self) -> SceneGraphSupplementEnvelope:
        if self.initial_scene_slot is None and self.initial_scene_source_block_ids:
            raise ValueError("Initial-scene evidence requires an initial scene selection")
        if self.initial_scene_slot is not None and not self.initial_scene_source_block_ids:
            raise ValueError("An initial scene selection requires source evidence")
        undirected_pairs: set[frozenset[int]] = set()
        directed_pairs: set[tuple[int, int]] = set()
        for link in self.links:
            pair = frozenset((link.from_slot, link.to_slot))
            direction = (link.from_slot, link.to_slot)
            reverse = (link.to_slot, link.from_slot)
            if link.one_way:
                if pair in undirected_pairs or direction in directed_pairs:
                    raise ValueError("Scene route selections must be unique")
                directed_pairs.add(direction)
                continue
            if (
                pair in undirected_pairs
                or direction in directed_pairs
                or reverse in directed_pairs
            ):
                raise ValueError("A bidirectional scene route must be selected once")
            undirected_pairs.add(pair)
        return self


@dataclass(frozen=True)
class SceneGraphSourceContext:
    """Server-owned document structure used to validate selected evidence."""

    title: str = ""
    section_path: tuple[str, ...] = ()


_DIRECT_ROUTE = re.compile(
    r"(?:通往|连接(?:着|到)?|相连|相邻|毗邻|进入|走进|前往|"
    r"可(?:以)?(?:直接)?到(?:达)?|抵达|到达|"
    r"\b(?:leads?|connects?)\s+(?:directly\s+)?(?:to|with)\b|"
    r"\bis\s+adjacent\s+to\b|\b(?:enter|enters|entered|go|goes|travel|travels)\s+to\b)",
    re.IGNORECASE,
)
_CONDITIONAL_ROUTE = re.compile(
    r"(?:如果|若|成功|检定|发现|秘密|隐藏|解锁|打开|打穿|破坏|"
    r"\b(?:if|when|unless|after|discover(?:ed)?|unlock(?:ed)?|secret|hidden)\b)",
    re.IGNORECASE,
)
_ALTERNATIVE_DESTINATIONS = re.compile(
    r"(?:也可以|或者|或是|任选|选择|由(?:玩家|调查员|你们)决定|"
    r"\b(?:either|alternatively|choose|choice|optionally)\b|\bor\b)",
    re.IGNORECASE,
)
_PLAYER_SUBJECT = re.compile(
    r"(?:调查员(?:们)?|玩家|你们|\b(?:investigators?|players?|characters?|you)\b)",
    re.IGNORECASE,
)
_ENTRY_ASSERTION = re.compile(
    r"(?:开始|开场|起始|从此出发|\b(?:begin|begins|start|starts|open|opens)\b)",
    re.IGNORECASE,
)
_OPENING_SECTION = re.compile(
    r"^\W*(?:开场(?:场景)?|起始(?:场景)?|序幕|"
    r"opening\s+scene|starting\s+scene|scenario\s+opening)\W*$",
    re.IGNORECASE,
)
_PRESENT_LOCATION_ASSERTION = re.compile(
    r"(?:位于|身处|聚集(?:在)?|集合(?:在)?|住在|站在|等候(?:在)?|"
    r"\b(?:gather(?:ed)?|meet|live|stand|wait|find yourselves)\b|"
    r"\b(?:are|is)\s+(?:all\s+)?(?:at|in|inside|outside|within)\b)",
    re.IGNORECASE,
)
_TITLE_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_TITLE_NOISE_TOKENS = frozenset({"a", "an", "the", "s"})
_SENTENCE_BOUNDARY = re.compile(
    r"(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bDr\.)(?<=[。！？.!?])\s*|[\r\n]+",
    re.IGNORECASE,
)


def opening_scene_section_key(context: SceneGraphSourceContext) -> str | None:
    """Return a normalized structural opening heading, if one is explicit."""

    for value in reversed((*context.section_path, context.title)):
        normalized = value.strip().casefold()
        if normalized and _OPENING_SECTION.fullmatch(normalized):
            return normalized.strip(" <>[]{}:：-—")
    return None


def _source_mentions_location_title(title: str, sentence: str) -> bool:
    """Match bounded lexical variants such as “X's room” / “room of X”."""

    if re.search(re.escape(title.strip()), sentence, re.IGNORECASE):
        return True
    title_tokens = tuple(
        token
        for token in _TITLE_TOKEN.findall(title.casefold())
        if token not in _TITLE_NOISE_TOKENS
    )
    # One-word display names must remain exact: fuzzy matching “Room” or
    # “House” would collapse distinct opaque location slots.
    if len(title_tokens) < 2:
        return False
    sentence_tokens = tuple(
        (match.group(), match.start(), match.end())
        for match in _TITLE_TOKEN.finditer(sentence.casefold())
    )

    def ordered_span(tokens: tuple[str, ...]) -> tuple[int, int] | None:
        cursor = 0
        first_start: int | None = None
        last_end = 0
        for token in tokens:
            match = next(
                (
                    item
                    for item in sentence_tokens[cursor:]
                    if item[0] == token
                ),
                None,
            )
            if match is None:
                return None
            index = sentence_tokens.index(match, cursor)
            cursor = index + 1
            first_start = match[1] if first_start is None else first_start
            last_end = match[2]
        assert first_start is not None
        return first_start, last_end

    direct = ordered_span(title_tokens)
    if direct is not None and direct[1] - direct[0] <= 80:
        return True
    # English and Chinese source prose may invert a possessive display title:
    # “Gardiner's Room” -> “the room of Mr. James Gardiner” / “Gardiner 的房间”.
    kind = title_tokens[-1]
    qualifiers = title_tokens[:-1]
    inverted = ordered_span((kind, *qualifiers))
    if inverted is None or inverted[1] - inverted[0] > 80:
        return False
    relation = sentence[inverted[0] : inverted[1]].casefold()
    return bool(re.search(r"(?:\bof\b|的)", relation))


def evidence_asserts_initial_scene(
    title: str,
    source_ids: tuple[str, ...],
    *,
    source_refs: Mapping[str, SourceRef],
    source_texts: Mapping[str, str],
    source_contexts: Mapping[str, SceneGraphSourceContext],
) -> bool:
    """Require an actual player entry assertion, not a mentioned objective."""

    for source_id in source_ids:
        text = source_texts[source_id]
        for sentence in _SENTENCE_BOUNDARY.split(text):
            if (
                _source_mentions_location_title(title, sentence)
                and _PLAYER_SUBJECT.search(sentence)
                and _ENTRY_ASSERTION.search(sentence)
            ):
                return True

    # Extractors commonly split a semantic heading from its read-aloud body.
    # Accept that structure only when the selected body explicitly places the
    # players at the location and the opening-section provenance is local.
    opening_items = tuple(
        (
            source_id,
            source_refs[source_id],
            key,
        )
        for source_id in source_ids
        if (key := opening_scene_section_key(source_contexts[source_id])) is not None
    )
    for body_id in source_ids:
        body_ref = source_refs[body_id]
        body_key = opening_scene_section_key(source_contexts[body_id])
        if body_key is None:
            continue
        for sentence in _SENTENCE_BOUNDARY.split(source_texts[body_id]):
            if not (
                _source_mentions_location_title(title, sentence)
                and _PLAYER_SUBJECT.search(sentence)
                and _PRESENT_LOCATION_ASSERTION.search(sentence)
            ):
                continue
            if any(
                opening_key == body_key
                and opening_ref.document_id == body_ref.document_id
                and (
                    opening_ref.page is None
                    or body_ref.page is None
                    or abs(opening_ref.page - body_ref.page) <= 1
                )
                for _, opening_ref, opening_key in opening_items
            ):
                return True
    return False


def _evidence_asserts_direct_route(
    from_title: str,
    to_title: str,
    texts: tuple[str, ...],
    *,
    allow_conditional: bool = False,
) -> bool:
    """Require both endpoints and an unconditional direct relation in one block."""

    if from_title.strip().casefold() == to_title.strip().casefold():
        # Equal display titles cannot prove which opaque duplicate slot is meant.
        return False
    start_pattern = re.compile(re.escape(from_title.strip()), re.IGNORECASE)
    end_pattern = re.compile(re.escape(to_title.strip()), re.IGNORECASE)
    for text in texts:
        for sentence in _SENTENCE_BOUNDARY.split(text):
            if not allow_conditional and _CONDITIONAL_ROUTE.search(sentence):
                continue
            # A list of investigation destinations grants player choice, not
            # physical adjacency between those destinations. Such choices are
            # materialized by the investigation-hub boundary instead.
            if _ALTERNATIVE_DESTINATIONS.search(sentence):
                continue
            starts = tuple(start_pattern.finditer(sentence))
            ends = tuple(end_pattern.finditer(sentence))
            for start in starts:
                for end in ends:
                    left, right = sorted((start, end), key=lambda item: item.start())
                    gap = sentence[left.end() : right.start()]
                    if len(gap) <= 80 and _DIRECT_ROUTE.search(gap):
                        return True
    return False


def retain_source_grounded_location_links(
    contract: ScenarioContract,
    *,
    source_texts: Mapping[str, str],
) -> ScenarioContract:
    """Remove authored links whose own citations do not prove their endpoints.

    Model-authored base links cross the same authority boundary as topology
    supplements.  A conditional source sentence is only eligible when the link
    itself carries a condition; the independent review and compiler continue to
    validate that condition.
    """

    location_by_id = {item.location_id: item for item in contract.locations}
    retained: list[LocationLink] = []
    for link in contract.location_links:
        start = location_by_id.get(link.from_location_id)
        end = location_by_id.get(link.to_location_id)
        texts = tuple(
            source_texts[ref.source_block_id]
            for ref in link.source_refs
            if ref.source_block_id in source_texts
        )
        if (
            start is not None
            and end is not None
            and texts
            and _evidence_asserts_direct_route(
                start.title,
                end.title,
                texts,
                allow_conditional=bool(link.preconditions),
            )
        ):
            retained.append(link)
    if len(retained) == len(contract.location_links):
        return contract
    return contract.model_copy(update={"location_links": tuple(retained)})


def scene_graph_location_catalog(
    contract: ScenarioContract,
) -> tuple[SceneGraphLocationCandidate, ...]:
    """Expose titles and provenance, but never runtime IDs or visibility."""

    if len(contract.locations) > 256:
        raise ValueError("Scene-graph location catalog exceeds bounded slot capacity")
    return tuple(
        SceneGraphLocationCandidate(
            slot=slot,
            title=location.title,
            source_block_ids=tuple(
                dict.fromkeys(ref.source_block_id for ref in location.source_refs)
            ),
        )
        for slot, location in enumerate(contract.locations)
    )


def apply_scene_graph_envelope(
    envelope: SceneGraphSupplementEnvelope,
    *,
    contract: ScenarioContract,
    source_refs: Mapping[str, SourceRef],
    source_texts: Mapping[str, str],
    source_contexts: Mapping[str, SceneGraphSourceContext] | None = None,
) -> ScenarioContract:
    """Add a bounded entry and routes to an already assembled strict contract.

    Coverage supplements run after initial assembly, so this companion path is
    intentionally strict-contract native.  It preserves every authored record,
    changes visibility only for the selected entry, and binds each new route to
    exact server-owned source locators. The subsequent independent review is
    responsible for rejecting secret or conditional destinations.
    """

    location_catalog = tuple(contract.locations)
    source_contexts = source_contexts or {
        source_id: SceneGraphSourceContext()
        for source_id in source_texts
    }
    if not location_catalog:
        raise ValueError("Scene-graph repair requires authored locations")

    def location_at(slot: int) -> LocationSpec:
        if slot >= len(location_catalog):
            raise ValueError(f"Unknown scene-graph location slot: {slot}")
        return location_catalog[slot]

    def refs_for(source_ids: tuple[str, ...]) -> tuple[SourceRef, ...]:
        unknown = sorted(set(source_ids) - source_refs.keys())
        if unknown:
            raise ValueError(
                "Scene-graph repair cited evidence outside its catalog: "
                + ", ".join(unknown)
            )
        return tuple(source_refs[source_id] for source_id in dict.fromkeys(source_ids))

    def texts_for(source_ids: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(source_ids) - source_texts.keys())
        if unknown:
            raise ValueError(
                "Scene-graph repair lacks authoritative text for cited evidence: "
                + ", ".join(unknown)
            )
        return tuple(source_texts[source_id] for source_id in dict.fromkeys(source_ids))

    def contexts_for(source_ids: tuple[str, ...]) -> None:
        unknown = sorted(set(source_ids) - source_contexts.keys())
        if unknown:
            raise ValueError(
                "Scene-graph repair lacks source structure for cited evidence: "
                + ", ".join(unknown)
            )

    initial_scene_id = contract.initial_scene_id
    locations = list(location_catalog)
    if envelope.initial_scene_slot is not None:
        initial = location_at(envelope.initial_scene_slot)
        refs_for(envelope.initial_scene_source_block_ids)
        texts_for(envelope.initial_scene_source_block_ids)
        contexts_for(envelope.initial_scene_source_block_ids)
        if not evidence_asserts_initial_scene(
            initial.title,
            envelope.initial_scene_source_block_ids,
            source_refs=source_refs,
            source_texts=source_texts,
            source_contexts=source_contexts,
        ):
            raise ValueError(
                "Scene-graph initial scene is not directly entailed by its evidence"
            )
        initial_scene_id = initial.location_id
        locations = [
            location.model_copy(update={"initial_visibility": "visited"})
            if location.location_id == initial_scene_id
            else location
            for location in locations
        ]

    links = list(contract.location_links)
    seen = {
        (link.from_location_id, link.to_location_id)
        for link in links
    }
    seen.update(
        (link.to_location_id, link.from_location_id)
        for link in links
        if not link.one_way
    )
    for selection in envelope.links:
        start = location_at(selection.from_slot).location_id
        end = location_at(selection.to_slot).location_id
        references = refs_for(selection.source_block_ids)
        start_location = location_at(selection.from_slot)
        end_location = location_at(selection.to_slot)
        if not _evidence_asserts_direct_route(
            start_location.title,
            end_location.title,
            texts_for(selection.source_block_ids),
        ):
            raise ValueError(
                "Scene-graph route endpoints are not directly entailed by their evidence"
            )
        if (start, end) in seen:
            continue
        links.append(
            LocationLink(
                from_location_id=start,
                to_location_id=end,
                one_way=selection.one_way,
                source_refs=references,
            )
        )
        seen.add((start, end))
        if not selection.one_way:
            seen.add((end, start))

    payload = contract.model_dump(mode="python")
    payload.update({
        "initial_scene_id": initial_scene_id,
        "locations": tuple(locations),
        "location_links": tuple(links),
    })
    return ScenarioContract.model_validate(payload)


__all__ = [
    "SceneGraphLinkSelection",
    "SceneGraphLocationCandidate",
    "SceneGraphSourceContext",
    "SceneGraphSupplementEnvelope",
    "apply_scene_graph_envelope",
    "evidence_asserts_initial_scene",
    "opening_scene_section_key",
    "retain_source_grounded_location_links",
    "scene_graph_location_catalog",
]
