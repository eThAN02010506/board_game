"""Deterministic convergence for duplicate authored scenario entities."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ai_kp.platform.resolution.contracts import (
    EntityCanonicalProfile,
    EntityDerivedProfile,
    EntitySpec,
    ScenarioContract,
)


@dataclass(frozen=True, slots=True)
class EntityIdentityConvergence:
    contract: ScenarioContract
    replacements: dict[str, str]
    diagnostics: tuple[str, ...]


_TITLE_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_TITLE_DECORATION = re.compile(r"[\W_]+", re.UNICODE)
_MANIFESTATION_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "corpse",
        re.compile(r"(?:尸体|遗体|肉身|遗骸|躯体)|\b(?:corpse|body|remains)\b", re.IGNORECASE),
    ),
    (
        "spirit",
        re.compile(r"(?:灵体|幽灵|鬼魂|魂魄)|\b(?:spirit|ghost|specter|spectre)\b", re.IGNORECASE),
    ),
    (
        "writing",
        re.compile(
            r"(?:日记|日志|笔记|书信|手稿)|\b(?:diar(?:y|ies)|journal|notes?|letters?|manuscript)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "depiction",
        re.compile(
            r"(?:画像|肖像|雕像|照片)|\b(?:portrait|painting|statue|photograph)\b", re.IGNORECASE
        ),
    ),
    (
        "item",
        re.compile(
            r"(?:武器|匕首|刀|遗物|物品)|\b(?:weapon|dagger|knife|relic|item)\b", re.IGNORECASE
        ),
    ),
    (
        "projection",
        re.compile(r"(?:分身|化身|投影)|\b(?:avatar|manifestation|projection)\b", re.IGNORECASE),
    ),
)


def canonical_entity_title(title: str) -> str:
    """Normalize only formatting distinctions, never semantic vocabulary."""

    normalized = unicodedata.normalize("NFKC", title).casefold()
    return "".join(_TITLE_DECORATION.split(normalized))


def _title_tokens(title: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return tuple(token for token in _TITLE_TOKEN.findall(normalized) if token)


def _manifestation_kind(title: str) -> str:
    matches = tuple(kind for kind, pattern in _MANIFESTATION_MARKERS if pattern.search(title))
    return "+".join(matches) if matches else "identity"


def _descriptor_base(title: str) -> str | None:
    """Return an explicit punctuation-delimited base name, if present."""

    parts = re.split(r"[，,（(：:—–]", title, maxsplit=1)
    return parts[0].strip() if len(parts) == 2 and parts[0].strip() else None


def _merge_values(
    records: tuple[EntitySpec, ...],
    field: str,
    *,
    maximum: int,
    diagnostics: list[str],
    canonical_id: str,
) -> tuple[str, ...]:
    profile_name, value_field = field.split(".", 1)
    values = tuple(
        dict.fromkeys(
            value
            for record in sorted(records, key=lambda item: item.entity_id)
            for value in getattr(getattr(record, profile_name), value_field)
            if value
        )
    )
    if len(values) > maximum:
        diagnostics.append(
            f"Entity identity profile was bounded after merge: {canonical_id}/{field}"
        )
    return values[:maximum]


def _choose_title(records: tuple[EntitySpec, ...]) -> str:
    return min(
        (item.title for item in records),
        key=lambda title: (
            0 if len(_title_tokens(title)) >= 2 else 1,
            len(_title_tokens(title)),
            len(canonical_entity_title(title)),
            unicodedata.normalize("NFKC", title).casefold(),
        ),
    )


def _merge_group(records: tuple[EntitySpec, ...], diagnostics: list[str]) -> EntitySpec:
    ordered = tuple(sorted(records, key=lambda item: item.entity_id))
    canonical_id = ordered[0].entity_id
    title = _choose_title(ordered)
    title_record = min(
        (item for item in ordered if item.title == title),
        key=lambda item: item.entity_id,
    )
    locations = tuple(
        dict.fromkeys(
            item.initial_location_id for item in ordered if item.initial_location_id is not None
        )
    )
    location_id = locations[0] if len(locations) == 1 else None
    if len(locations) > 1:
        diagnostics.append(
            "Conflicting duplicate entity locations were cleared: "
            f"{canonical_id} <- {', '.join(item.entity_id for item in ordered)}"
        )
    runtimes = tuple(
        dict.fromkeys(
            item.initial_runtime.model_dump_json()
            for item in ordered
            if item.initial_runtime is not None
        )
    )
    initial_runtime = (
        next(item.initial_runtime for item in ordered if item.initial_runtime is not None)
        if len(runtimes) == 1
        and next(
            item.initial_runtime for item in ordered if item.initial_runtime is not None
        ).location_id
        == location_id
        else None
    )
    if len(runtimes) > 1:
        diagnostics.append(f"Conflicting duplicate entity runtime was cleared: {canonical_id}")

    def summary(profile_name: str) -> str:
        chosen = getattr(title_record, profile_name).summary
        if chosen:
            return chosen
        return next(
            (
                getattr(item, profile_name).summary
                for item in ordered
                if getattr(item, profile_name).summary
            ),
            "",
        )

    canonical = EntityCanonicalProfile(
        summary=summary("canonical_profile"),
        known_facts=_merge_values(
            ordered,
            "canonical_profile.known_facts",
            maximum=32,
            diagnostics=diagnostics,
            canonical_id=canonical_id,
        ),
        secrets=_merge_values(
            ordered,
            "canonical_profile.secrets",
            maximum=24,
            diagnostics=diagnostics,
            canonical_id=canonical_id,
        ),
        behavioral_directives=_merge_values(
            ordered,
            "canonical_profile.behavioral_directives",
            maximum=16,
            diagnostics=diagnostics,
            canonical_id=canonical_id,
        ),
        boundaries=_merge_values(
            ordered,
            "canonical_profile.boundaries",
            maximum=16,
            diagnostics=diagnostics,
            canonical_id=canonical_id,
        ),
    )
    derived = EntityDerivedProfile(
        traits=_merge_values(
            ordered,
            "derived_profile.traits",
            maximum=12,
            diagnostics=diagnostics,
            canonical_id=canonical_id,
        ),
        speech_style=_merge_values(
            ordered,
            "derived_profile.speech_style",
            maximum=12,
            diagnostics=diagnostics,
            canonical_id=canonical_id,
        ),
        motivations=_merge_values(
            ordered,
            "derived_profile.motivations",
            maximum=12,
            diagnostics=diagnostics,
            canonical_id=canonical_id,
        ),
        fears=_merge_values(
            ordered,
            "derived_profile.fears",
            maximum=12,
            diagnostics=diagnostics,
            canonical_id=canonical_id,
        ),
        mannerisms=_merge_values(
            ordered,
            "derived_profile.mannerisms",
            maximum=12,
            diagnostics=diagnostics,
            canonical_id=canonical_id,
        ),
    )
    source_refs = tuple(
        sorted(
            dict.fromkeys(ref for item in ordered for ref in item.source_refs),
            key=lambda ref: (
                ref.document_id,
                ref.page or 0,
                ref.paragraph or 0,
                ref.source_block_id,
            ),
        )
    )
    return title_record.model_copy(
        update={
            "entity_id": canonical_id,
            "title": title,
            "initial_location_id": location_id,
            "initial_runtime": initial_runtime,
            "canonical_profile": canonical,
            "derived_profile": derived,
            "source_refs": source_refs,
        }
    )


def _rewrite_entity_references(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_rewrite_entity_references(item, replacements) for item in value]
    if not isinstance(value, dict):
        return value
    rewritten: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"entity_id", "target_entity_id", "speaker_entity_id"} and isinstance(item, str):
            rewritten[key] = replacements.get(item, item)
            continue
        if key in {"entity_ids", "target_entity_ids", "speaker_entity_ids"} and isinstance(
            item, list
        ):
            rewritten[key] = [
                replacements.get(member, member) if isinstance(member, str) else member
                for member in item
            ]
            continue
        if key in {"path", "source_path"} and isinstance(item, str):
            rewritten_path = item
            for previous, canonical in replacements.items():
                prefix = f"entities.{previous}"
                if item == prefix or item.startswith(f"{prefix}."):
                    rewritten_path = f"entities.{canonical}{item[len(prefix) :]}"
                    break
            rewritten[key] = rewritten_path
            continue
        rewritten[key] = _rewrite_entity_references(item, replacements)
    return rewritten


def converge_scenario_entity_identities(
    contract: ScenarioContract,
) -> EntityIdentityConvergence:
    """Merge only formatting-equivalent or explicit unambiguous name variants."""

    entities = contract.entities
    if len(entities) < 2:
        return EntityIdentityConvergence(contract=contract, replacements={}, diagnostics=())
    parent = {item.entity_id: item.entity_id for item in entities}
    diagnostics: list[str] = []

    def find(entity_id: str) -> str:
        while parent[entity_id] != entity_id:
            parent[entity_id] = parent[parent[entity_id]]
            entity_id = parent[entity_id]
        return entity_id

    def union(first: EntitySpec, second: EntitySpec) -> None:
        # A name heuristic must not override an explicit provenance identity,
        # including by borrowing it for an unbound same-name record.
        if first.module_entity_id != second.module_entity_id:
            return
        if first.entity_type != second.entity_type:
            return
        if first.initial_status != second.initial_status:
            diagnostics.append(
                "Conflicting duplicate entity statuses were kept separate: "
                f"{first.entity_id}/{second.entity_id}"
            )
            return
        first_root, second_root = find(first.entity_id), find(second.entity_id)
        if first_root == second_root:
            return
        canonical = min(first_root, second_root)
        parent[first_root] = canonical
        parent[second_root] = canonical

    by_signature: dict[tuple[str, str, str], list[EntitySpec]] = defaultdict(list)
    types_by_title: dict[tuple[str, str], set[str]] = defaultdict(set)
    for entity in entities:
        manifestation = _manifestation_kind(entity.title)
        canonical_title = canonical_entity_title(entity.title)
        by_signature[
            (
                entity.entity_type,
                manifestation,
                canonical_title,
            )
        ].append(entity)
        types_by_title[(manifestation, canonical_title)].add(entity.entity_type)
    for (_, canonical_title), entity_types in types_by_title.items():
        if len(entity_types) > 1:
            diagnostics.append(
                "Format-equivalent entity title with conflicting record types was "
                f"kept separate: {canonical_title}/{','.join(sorted(entity_types))}"
            )
    for records in by_signature.values():
        for item in records[1:]:
            union(records[0], item)

    # Punctuation-delimited epithets are explicit expanded variants
    # ("Walter Corbitt" / "Walter Corbitt, the Undying").  Plain lexical
    # prefixes such as "New York" / "New York Times" are not identities.
    identity_entities = tuple(
        item for item in entities if _manifestation_kind(item.title) == "identity"
    )
    for index, first in enumerate(identity_entities):
        for second in identity_entities[index + 1 :]:
            first_base = _descriptor_base(first.title)
            second_base = _descriptor_base(second.title)
            if (
                first_base is not None
                and canonical_entity_title(first_base)
                == canonical_entity_title(second.title)
            ) or (
                second_base is not None
                and canonical_entity_title(second_base)
                == canonical_entity_title(first.title)
            ):
                union(first, second)

    # A one-token short name is accepted only when one distinct shortest full
    # name in the same entity type owns that exact token.
    for short in identity_entities:
        short_tokens = _title_tokens(short.title)
        if (
            short.entity_type not in {"npc", "creature"}
            or len(short_tokens) != 1
            or len(short_tokens[0]) < 2
        ):
            continue
        long_records = tuple(
            item
            for item in identity_entities
            if item.entity_type == short.entity_type
            and len(_title_tokens(item.title)) >= 2
            and short_tokens[0] in _title_tokens(item.title)
        )
        if not long_records:
            continue
        minimum = min(len(_title_tokens(item.title)) for item in long_records)
        roots = {
            _title_tokens(item.title)
            for item in long_records
            if len(_title_tokens(item.title)) == minimum
        }
        if len(roots) != 1:
            diagnostics.append(f"Ambiguous short entity name was kept separate: {short.title}")
            continue
        root = next(iter(roots))
        for item in long_records:
            if _title_tokens(item.title)[: len(root)] == root:
                union(short, item)

    groups: dict[str, list[EntitySpec]] = defaultdict(list)
    for entity in entities:
        groups[find(entity.entity_id)].append(entity)
    replacements: dict[str, str] = {}
    merged_entities: list[EntitySpec] = []
    for records in groups.values():
        ordered = tuple(sorted(records, key=lambda item: item.entity_id))
        if len(ordered) == 1:
            merged_entities.append(ordered[0])
            continue
        merged = _merge_group(ordered, diagnostics)
        merged_entities.append(merged)
        replacements.update(
            {
                item.entity_id: merged.entity_id
                for item in ordered
                if item.entity_id != merged.entity_id
            }
        )
        diagnostics.append(
            "Equivalent scenario entities were converged: "
            f"{merged.entity_id} <- {', '.join(item.entity_id for item in ordered)}"
        )
    if not replacements:
        return EntityIdentityConvergence(
            contract=contract,
            replacements={},
            diagnostics=tuple(dict.fromkeys(diagnostics)),
        )
    payload = _rewrite_entity_references(contract.model_dump(mode="json"), replacements)
    payload["entities"] = [
        item.model_dump(mode="json")
        for item in sorted(merged_entities, key=lambda item: item.entity_id)
    ]
    converged = ScenarioContract.model_validate(payload)
    return EntityIdentityConvergence(
        contract=converged,
        replacements=replacements,
        diagnostics=tuple(dict.fromkeys(diagnostics)),
    )


__all__ = [
    "EntityIdentityConvergence",
    "canonical_entity_title",
    "converge_scenario_entity_identities",
]
