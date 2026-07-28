"""Pure event-backed projection for the append-only world-fact ledger."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ai_kp.platform.facts.models import AppendOnlyCorrectionResult, WorldFact

FACT_SCHEMA_VERSION = "world-fact.v1"
FACT_ASSERTED_EVENT = "world_fact.asserted"
FACT_RETCONNED_EVENT = "world_fact.retconned"
RESERVED_FACT_EVENT_PREFIX = "world_fact."
FACT_EVENT_TYPES = frozenset({FACT_ASSERTED_EVENT, FACT_RETCONNED_EVENT})


def _identifier(value: str, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be text")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    if any(character.isspace() for character in normalized):
        raise ValueError(f"{field_name} cannot contain whitespace")
    return normalized


def _source_reference(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("source_reference must be an object")
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("source_reference must contain finite JSON data") from exc
    if len(encoded) > 8000:
        raise ValueError("source_reference cannot exceed 8000 encoded characters")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("source_reference must be an object")
    return decoded


@dataclass(frozen=True)
class FactLedgerEntry:
    """One validated fact revision encoded in the authoritative event stream."""

    campaign_id: str
    fact_key: str
    event_id: str
    revision: int
    fact: WorldFact
    asserted_by: str
    supersedes_event_id: str | None = None
    evidence_event_ids: tuple[str, ...] = ()
    source_reference: dict[str, Any] = field(default_factory=dict)
    happened_at: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "campaign_id", _identifier(self.campaign_id, "campaign_id")
        )
        object.__setattr__(self, "fact_key", _identifier(self.fact_key, "fact_key"))
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        if not isinstance(self.fact, WorldFact):
            raise TypeError("fact must be a WorldFact")
        if self.fact.fact_id != self.event_id:
            raise ValueError("fact_id must equal the authoritative event_id")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("revision must be a positive integer")

        normalized_supersedes = (
            _identifier(self.supersedes_event_id, "supersedes_event_id")
            if self.supersedes_event_id is not None
            else None
        )
        object.__setattr__(self, "supersedes_event_id", normalized_supersedes)
        if self.revision == 1 and normalized_supersedes is not None:
            raise ValueError("The first fact revision cannot supersede an event")
        if self.revision > 1 and normalized_supersedes is None:
            raise ValueError("Later fact revisions require supersedes_event_id")
        if self.fact.category == "retconned":
            if self.fact.supersedes_fact_id != normalized_supersedes:
                raise ValueError(
                    "A retconned fact must reference the superseded event in both layers"
                )
        elif self.fact.supersedes_fact_id is not None:
            raise ValueError("Only retconned facts may carry supersedes_fact_id")

        normalized_actor = unicodedata.normalize("NFKC", self.asserted_by).strip()
        if not normalized_actor or len(normalized_actor) > 200:
            raise ValueError("asserted_by must contain 1 to 200 characters")
        object.__setattr__(self, "asserted_by", normalized_actor)

        evidence = tuple(
            dict.fromkeys(
                _identifier(item, "evidence_event_id")
                for item in self.evidence_event_ids
            )
        )
        if len(evidence) > 20:
            raise ValueError("A fact can cite at most 20 evidence events")
        if self.event_id in evidence:
            raise ValueError("A fact event cannot cite itself as evidence")
        object.__setattr__(self, "evidence_event_ids", evidence)
        object.__setattr__(
            self,
            "source_reference",
            _source_reference(self.source_reference),
        )

    @property
    def event_type(self) -> str:
        return (
            FACT_RETCONNED_EVENT
            if self.fact.category == "retconned"
            else FACT_ASSERTED_EVENT
        )

    @property
    def storage_visibility(self) -> str:
        # Generic event readers do not understand character ownership. Store a
        # character belief as KP-only and expose it through the scoped fact projection.
        return "kp" if self.fact.visibility == "player" else self.fact.visibility

    @property
    def active(self) -> bool:
        return self.fact.category != "retconned"

    def event_payload(self) -> dict[str, Any]:
        return {
            "schema_version": FACT_SCHEMA_VERSION,
            "fact_key": self.fact_key,
            "revision": self.revision,
            "supersedes_event_id": self.supersedes_event_id,
            "asserted_by": self.asserted_by,
            "evidence_event_ids": list(self.evidence_event_ids),
            "source_reference": self.source_reference,
            "fact": self.fact.as_dict(),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "fact_key": self.fact_key,
            "event_id": self.event_id,
            "revision": self.revision,
            "active": self.active,
            "supersedes_event_id": self.supersedes_event_id,
            "asserted_by": self.asserted_by,
            "evidence_event_ids": list(self.evidence_event_ids),
            "source_reference": self.source_reference,
            "happened_at": self.happened_at,
            "created_at": self.created_at,
            "fact": self.fact.as_dict(),
        }

    @classmethod
    def from_event(cls, event: Mapping[str, Any]) -> FactLedgerEntry:
        event_type = event.get("event_type")
        if event_type not in FACT_EVENT_TYPES:
            raise ValueError(f"Unsupported world-fact event type: {event_type}")
        payload_value = event.get("payload", event.get("payload_json"))
        if isinstance(payload_value, str):
            try:
                payload = json.loads(payload_value)
            except json.JSONDecodeError as exc:
                raise ValueError("World-fact event payload is not valid JSON") from exc
        else:
            payload = payload_value
        if not isinstance(payload, dict):
            raise ValueError(  # noqa: TRY004
                "World-fact event payload must be an object"
            )
        if payload.get("schema_version") != FACT_SCHEMA_VERSION:
            raise ValueError("Unsupported world-fact schema version")
        fact_payload = payload.get("fact")
        if not isinstance(fact_payload, dict):
            raise ValueError(  # noqa: TRY004
                "World-fact event is missing its fact object"
            )
        fact = WorldFact(**fact_payload)
        expected_event_type = (
            FACT_RETCONNED_EVENT
            if fact.category == "retconned"
            else FACT_ASSERTED_EVENT
        )
        if event_type != expected_event_type:
            raise ValueError("World-fact category does not match its event type")
        entry = cls(
            campaign_id=str(event["campaign_id"]),
            fact_key=payload["fact_key"],
            event_id=str(event["id"]),
            revision=payload["revision"],
            fact=fact,
            asserted_by=payload["asserted_by"],
            supersedes_event_id=payload.get("supersedes_event_id"),
            evidence_event_ids=tuple(payload.get("evidence_event_ids") or ()),
            source_reference=payload.get("source_reference") or {},
            happened_at=event.get("happened_at"),
            created_at=event.get("created_at"),
        )
        if event.get("visibility") != entry.storage_visibility:
            raise ValueError("World-fact event storage visibility is inconsistent")
        return entry


def project_fact_heads(entries: Iterable[FactLedgerEntry]) -> list[FactLedgerEntry]:
    """Validate each chain and return its current head, including retconned heads."""

    by_key: dict[str, list[FactLedgerEntry]] = {}
    for entry in entries:
        by_key.setdefault(entry.fact_key, []).append(entry)

    heads: list[FactLedgerEntry] = []
    for fact_key, revisions in by_key.items():
        ordered = sorted(revisions, key=lambda item: (item.revision, item.event_id))
        first = ordered[0]
        if first.revision != 1 or first.supersedes_event_id is not None:
            raise ValueError(f"Fact chain {fact_key} does not start at revision 1")
        campaign_id = first.campaign_id
        identity = (
            first.fact.subject,
            first.fact.predicate,
            first.fact.visibility,
            first.fact.pc_id,
        )
        previous = first
        for current in ordered[1:]:
            if current.campaign_id != campaign_id:
                raise ValueError(f"Fact chain {fact_key} crosses campaigns")
            if current.revision != previous.revision + 1:
                raise ValueError(f"Fact chain {fact_key} has a revision gap or fork")
            if current.supersedes_event_id != previous.event_id:
                raise ValueError(f"Fact chain {fact_key} does not extend its current head")
            current_identity = (
                current.fact.subject,
                current.fact.predicate,
                current.fact.visibility,
                current.fact.pc_id,
            )
            if current_identity != identity:
                raise ValueError(f"Fact chain {fact_key} changed identity or visibility")
            if current.fact.category == "retconned":
                AppendOnlyCorrectionResult(previous.fact, current.fact)
            previous = current
        heads.append(previous)
    return sorted(
        heads,
        key=lambda item: (item.created_at or "", item.event_id),
        reverse=True,
    )


def visible_fact_heads(
    entries: Iterable[FactLedgerEntry],
    *,
    role: str,
    pc_id: str | None,
    include_retconned: bool = False,
) -> list[FactLedgerEntry]:
    """Return current heads after role/PC projection and optional audit history."""

    if role not in {"kp", "player"}:
        raise ValueError("World-fact view role must be kp or player")
    visible: list[FactLedgerEntry] = []
    for entry in project_fact_heads(entries):
        if not include_retconned and not entry.active:
            continue
        if role == "kp" or entry.fact.visibility == "table" or (
            entry.fact.visibility == "player"
            and pc_id is not None
            and entry.fact.pc_id == pc_id
        ):
            visible.append(entry)
    return visible


__all__ = [
    "FACT_ASSERTED_EVENT",
    "FACT_EVENT_TYPES",
    "FACT_RETCONNED_EVENT",
    "FACT_SCHEMA_VERSION",
    "RESERVED_FACT_EVENT_PREFIX",
    "FactLedgerEntry",
    "project_fact_heads",
    "visible_fact_heads",
]
