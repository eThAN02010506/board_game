"""Materialize explicit source scene headings as model-independent IR locations."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Mapping

from ai_kp.platform.modules.section_ancestry import inferred_scene_key
from ai_kp.platform.resolution.scenario_action_scope import canonical_location_alias
from ai_kp.platform.resolution.scenario_ir_models import IrLocation, ScenarioIrBatch

_MAX_LOCATIONS_PER_BATCH = 12


def materialize_source_scene_locations(
    batches: tuple[ScenarioIrBatch, ...],
    *,
    source_texts: Mapping[str, str] | None,
    source_scene_keys: Mapping[str, str] | None,
    source_semantic_kinds: Mapping[str, str] | None,
    assumptions: list[str],
) -> tuple[ScenarioIrBatch, ...]:
    """Append locations proven by explicit, server-classified scene headings.

    Model action slots have already been resolved when this function runs, so
    these server-owned batches cannot shift a partition-local slot index.  A
    scene key alone is insufficient: its source block must also be classified
    as ``scene`` and its own text must be that exact numbered scene heading.
    """

    if not source_scene_keys or not source_semantic_kinds:
        return batches

    locations: list[IrLocation] = []
    seen_identities: set[str] = set()
    for source_id, scene_key in source_scene_keys.items():
        if source_semantic_kinds.get(source_id) != "scene":
            continue
        title = _normalized_title(scene_key)
        identity = canonical_location_alias(title)
        if not identity or identity in seen_identities:
            continue
        source_text = (source_texts or {}).get(source_id, "")
        parsed_scene_key = inferred_scene_key((source_text,))
        if canonical_location_alias(parsed_scene_key or "") != identity:
            continue
        seen_identities.add(identity)
        location_id = _stable_location_id(title)
        locations.append(
            IrLocation(
                id=location_id,
                title=title,
                source_block_ids=(source_id,),
            )
        )

    if not locations:
        return batches
    assumptions.append(
        "Server source scene locations materialized: "
        + ", ".join(f"{item.id} ({item.title})" for item in locations)
    )
    source_batches = tuple(
        ScenarioIrBatch(locations=tuple(locations[start : start + _MAX_LOCATIONS_PER_BATCH]))
        for start in range(0, len(locations), _MAX_LOCATIONS_PER_BATCH)
    )
    return (*batches, *source_batches)


def _normalized_title(value: str) -> str:
    return unicodedata.normalize("NFKC", " ".join(value.split())).strip()[:240]


def _stable_location_id(title: str) -> str:
    digest = hashlib.sha256(canonical_location_alias(title).encode("utf-8")).hexdigest()
    return f"source_scene_{digest[:24]}"


__all__ = ["materialize_source_scene_locations"]
