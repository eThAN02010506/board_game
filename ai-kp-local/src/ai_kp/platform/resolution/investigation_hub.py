"""Materialize source-declared investigation choices as an executable phase.

An investigation choice is not a physical ``LocationLink``.  This module turns
an explicit player-facing choice into a small set of ordinary, authoritative
``set_scene`` operators while keeping the reducer and runtime protocol unchanged.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    LocationSpec,
    ScenarioContract,
    SourceRef,
    StateCondition,
    WorldCommand,
)

_CHOICE = re.compile(
    r"(?:可以.{0,120}(?:也可以|或者|或).{0,120}(?:由你们决定|自由选择|任意选择)|"
    r"(?:choose|decide).{0,160}(?:where|which|between|among))",
    re.IGNORECASE | re.DOTALL,
)
_DIRECT_ENTRY = re.compile(
    r"(?:直接(?:进入|前往|去|到)|(?:directly\s+)?(?:enter|go|travel)\s+to)",
    re.IGNORECASE,
)
_FREE_MOVEMENT = re.compile(
    r"(?:场景之间的移动.{0,80}直接.{0,40}抵达新场景|"
    r"(?:move|travel).{0,80}between.{0,80}(?:scenes|locations).{0,80}direct)",
    re.IGNORECASE | re.DOTALL,
)
_SECRET_OR_CONDITIONAL = re.compile(
    r"(?:秘密|隐藏|检定|成功后|发现后|打穿|解锁|"
    r"\b(?:secret|hidden|if|when|after|check|discover|unlock)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InvestigationEvidence:
    """Server-owned source projection used by the deterministic materializer."""

    source_ref: SourceRef
    title: str
    text: str
    section_path: tuple[str, ...] = ()
    scene_key: str | None = None


def _canonical_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[\s:：,，.。·・'\"‘’“”()（）]+", "", normalized)
    return normalized


def _source_group(evidence: InvestigationEvidence) -> tuple[str, ...]:
    return tuple(_canonical_text(item) for item in evidence.section_path if item.strip()) or (
        _canonical_text(evidence.title),
    )


def _mentioned_location_ids(
    text: str,
    locations: tuple[LocationSpec, ...],
) -> tuple[str, ...]:
    canonical = _canonical_text(text)
    by_title: dict[str, list[LocationSpec]] = {}
    for location in locations:
        by_title.setdefault(_canonical_text(location.title), []).append(location)
    return tuple(
        records[0].location_id
        for title, records in by_title.items()
        if title and title in canonical and len(records) == 1
    )


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def materialize_investigation_hub(
    contract: ScenarioContract,
    evidence: Iterable[InvestigationEvidence],
) -> ScenarioContract:
    """Add one source-grounded investigation phase when no entry scene exists.

    The function is deliberately deterministic and idempotent.  Ambiguous
    location titles, conditional/secret prose, and objectives merely mentioned
    by the source do not become destinations.
    """

    if contract.initial_scene_id is not None or not contract.locations:
        return contract
    evidence_items = tuple(evidence)
    choice_candidates: dict[
        tuple[str, tuple[str, ...]], list[InvestigationEvidence]
    ] = {}
    for item in evidence_items:
        if not _CHOICE.search(item.text) or _SECRET_OR_CONDITIONAL.search(item.text):
            continue
        destinations = tuple(
            dict.fromkeys(_mentioned_location_ids(item.text, contract.locations))
        )
        if len(destinations) >= 2:
            signature = (_canonical_text(item.text), destinations[:8])
            choice_candidates.setdefault(signature, []).append(item)
    if len(choice_candidates) != 1:
        return contract

    (_choice_text, selectable), duplicate_choices = next(
        iter(choice_candidates.items())
    )
    choice_refs = tuple(
        {
            item.source_ref.source_block_id: item.source_ref
            for item in duplicate_choices
        }[source_block_id]
        for source_block_id in sorted(
            {item.source_ref.source_block_id for item in duplicate_choices}
        )
    )
    representative = min(
        duplicate_choices,
        key=lambda item: (
            item.source_ref.page or 1_000_000_000,
            item.source_ref.paragraph or 1_000_000_000,
            item.source_ref.source_block_id,
        ),
    )
    representative_group = _source_group(representative)
    same_group = tuple(
        item for item in evidence_items if _source_group(item) == representative_group
    )
    movement_evidence = next(
        (item for item in same_group if _FREE_MOVEMENT.search(item.text)),
        None,
    )
    allow_return = movement_evidence is not None

    # An explicitly direct destination in the same source section may be an
    # irreversible phase exit.  It must name one unique existing location.
    exit_refs: dict[str, tuple[SourceRef, ...]] = {}
    for item in same_group:
        if not _DIRECT_ENTRY.search(item.text) or _SECRET_OR_CONDITIONAL.search(item.text):
            continue
        mentioned = _mentioned_location_ids(item.text, contract.locations)
        if len(mentioned) == 1 and mentioned[0] not in selectable:
            exit_refs.setdefault(mentioned[0], (item.source_ref,))

    location_by_id = {item.location_id: item for item in contract.locations}
    local_scene_keys = {
        _canonical_text(item.scene_key)
        for item in duplicate_choices
        if item.scene_key and item.scene_key.strip()
    }
    source_phase_candidates = tuple(
        item
        for item in contract.locations
        if _canonical_text(item.title) in local_scene_keys
    )
    source_phase = (
        source_phase_candidates[0]
        if len(local_scene_keys) == 1 and len(source_phase_candidates) == 1
        else None
    )
    if source_phase is None:
        phase_id = _stable_id(
            "system_investigation_phase",
            contract.contract_id,
            *(item.source_block_id for item in choice_refs),
        )
        phase = LocationSpec(
            location_id=phase_id,
            title="调查准备阶段",
            initial_visibility="visited",
            tags=("system_phase", "investigation_choice"),
            source_refs=choice_refs,
        )
    else:
        phase_id = source_phase.location_id
        phase = source_phase.model_copy(update={"initial_visibility": "visited"})
    destination_ids = tuple(dict.fromkeys((*selectable, *exit_refs)))
    generated: list[ActionOperator] = []

    def travel_operator(
        origin: str,
        destination: str,
        refs: tuple[SourceRef, ...],
    ) -> ActionOperator:
        target = location_by_id[destination]
        operator_id = _stable_id(
            "system_investigation_travel",
            contract.contract_id,
            origin,
            destination,
            *(item.source_block_id for item in refs),
        )
        return ActionOperator(
            operator_id=operator_id,
            title=f"前往{target.title}",
            intent_hints=(f"前往{target.title}", f"去{target.title}"),
            public_setup=f"调查员们决定前往{target.title}。",
            policy="automatic",
            preconditions=(StateCondition(path="scene_id", operator="eq", value=origin),),
            success_commands=(WorldCommand(kind="set_scene", value=destination),),
            rationale="Server-materialized source-declared investigation choice.",
            maximum_effect="Only changes the current public scene.",
            source_refs=refs,
        )

    for destination in destination_ids:
        generated.append(
            travel_operator(
                phase_id,
                destination,
                exit_refs.get(destination, choice_refs),
            )
        )
    if allow_return:
        for origin in selectable:
            return_id = _stable_id(
                "system_investigation_return",
                contract.contract_id,
                origin,
                phase_id,
                movement_evidence.source_ref.source_block_id,
            )
            generated.append(
                ActionOperator(
                    operator_id=return_id,
                    title="继续选择调查地点",
                    intent_hints=("继续调查", "去其他调查地点"),
                    public_setup="调查员们决定继续查访其他已知地点。",
                    policy="automatic",
                    preconditions=(
                        StateCondition(path="scene_id", operator="eq", value=origin),
                    ),
                    success_commands=(WorldCommand(kind="set_scene", value=phase_id),),
                    rationale="Server-materialized source-declared free investigation movement.",
                    maximum_effect="Returns to the public investigation choice phase.",
                    source_refs=(movement_evidence.source_ref,),
                )
            )

    locations = [] if source_phase is not None else [phase]
    visible_ids = set(destination_ids)
    locations.extend(
        phase
        if item.location_id == phase_id
        else item.model_copy(update={"initial_visibility": "known"})
        if item.location_id in visible_ids
        else item
        for item in contract.locations
    )
    existing = {item.operator_id for item in contract.operators}
    operators = (*contract.operators, *(item for item in generated if item.operator_id not in existing))
    return contract.model_copy(
        update={
            "initial_scene_id": phase_id,
            "locations": tuple(locations),
            "operators": tuple(operators),
        }
    )


__all__ = ["InvestigationEvidence", "materialize_investigation_hub"]
