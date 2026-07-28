"""Immutable domain models for the append-only world-fact ledger."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

FactCategory = Literal[
    "canonical_fact",
    "kp_secret",
    "character_belief",
    "rumor",
    "ai_hypothesis",
    "retconned",
]
FactVisibility = Literal["table", "kp", "player"]

FACT_CATEGORIES: frozenset[str] = frozenset(
    {
        "canonical_fact",
        "kp_secret",
        "character_belief",
        "rumor",
        "ai_hypothesis",
        "retconned",
    }
)
FACT_VISIBILITIES: frozenset[str] = frozenset({"table", "kp", "player"})
_FIELD_LIMITS = {
    "subject": 200,
    "predicate": 120,
    "object_text": 4000,
}


def normalize_fact_text(value: str, *, field_name: str) -> str:
    """Return one canonical, non-empty text representation for a fact field."""

    if type(value) is not str:
        raise ValueError(f"{field_name} must be text")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    max_length = _FIELD_LIMITS.get(field_name)
    if max_length is not None and len(normalized) > max_length:
        raise ValueError(f"{field_name} cannot exceed {max_length} characters")
    return normalized


def _normalize_identifier(value: str, *, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be text")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    if any(character.isspace() for character in normalized):
        raise ValueError(f"{field_name} cannot contain whitespace")
    return normalized


def _normalize_optional_identifier(
    value: str | None,
    *,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    return _normalize_identifier(value, field_name=field_name)


@dataclass(frozen=True)
class WorldFact:
    """One immutable fact entry; corrections are represented by later entries."""

    fact_id: str
    category: FactCategory
    visibility: FactVisibility
    subject: str
    predicate: str
    object_text: str
    pc_id: str | None = None
    supersedes_fact_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fact_id",
            _normalize_identifier(self.fact_id, field_name="fact_id"),
        )
        if type(self.category) is not str or self.category not in FACT_CATEGORIES:
            raise ValueError(f"Unknown fact category: {self.category}")
        if type(self.visibility) is not str or self.visibility not in FACT_VISIBILITIES:
            raise ValueError(f"Unknown fact visibility: {self.visibility}")

        object.__setattr__(
            self,
            "subject",
            normalize_fact_text(self.subject, field_name="subject"),
        )
        object.__setattr__(
            self,
            "predicate",
            normalize_fact_text(self.predicate, field_name="predicate"),
        )
        object.__setattr__(
            self,
            "object_text",
            normalize_fact_text(self.object_text, field_name="object_text"),
        )
        object.__setattr__(
            self,
            "pc_id",
            _normalize_optional_identifier(self.pc_id, field_name="pc_id"),
        )
        object.__setattr__(
            self,
            "supersedes_fact_id",
            _normalize_optional_identifier(
                self.supersedes_fact_id,
                field_name="supersedes_fact_id",
            ),
        )

        if self.category == "canonical_fact":
            if self.visibility != "table":
                raise ValueError("canonical_fact visibility must be table")
            if self.pc_id is not None:
                raise ValueError("canonical_fact cannot target a PC")
        elif self.category == "kp_secret":
            if self.visibility != "kp":
                raise ValueError("kp_secret visibility must be kp")
            if self.pc_id is not None:
                raise ValueError("kp_secret cannot target a PC")
        elif self.category == "character_belief":
            if self.visibility != "player":
                raise ValueError("character_belief visibility must be player")
            if self.pc_id is None:
                raise ValueError("character_belief requires pc_id")
        elif self.category == "rumor":
            if self.visibility != "table":
                raise ValueError("rumor visibility must be table")
            if self.pc_id is not None:
                raise ValueError("rumor cannot target a PC")
        elif self.category == "ai_hypothesis":
            if self.visibility != "kp":
                raise ValueError("ai_hypothesis visibility must be kp")
            if self.pc_id is not None:
                raise ValueError("ai_hypothesis cannot target a PC")
        elif self.category == "retconned":
            if self.visibility == "player" and self.pc_id is None:
                raise ValueError("player-visible retconned facts require pc_id")
            if self.visibility != "player" and self.pc_id is not None:
                raise ValueError("only player-visible retconned facts can target a PC")

        if self.category == "retconned":
            if self.supersedes_fact_id is None:
                raise ValueError("retconned requires supersedes_fact_id")
            if self.supersedes_fact_id == self.fact_id:
                raise ValueError("A retconned fact cannot supersede itself")
        elif self.supersedes_fact_id is not None:
            raise ValueError("supersedes_fact_id is only valid for retconned")

    def as_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "category": self.category,
            "visibility": self.visibility,
            "subject": self.subject,
            "predicate": self.predicate,
            "object_text": self.object_text,
            "pc_id": self.pc_id,
            "supersedes_fact_id": self.supersedes_fact_id,
        }


def _validate_correction_fact(fact: WorldFact) -> str:
    if not isinstance(fact, WorldFact):
        raise TypeError("correction must be a WorldFact")
    if fact.category != "retconned" or fact.supersedes_fact_id is None:
        raise ValueError("correction must be a retconned fact")
    return fact.supersedes_fact_id


@dataclass(frozen=True)
class AppendOnlyCorrectionCommand:
    """Request to append a prepared correction without updating the old entry."""

    superseded_fact_id: str
    correction: WorldFact

    def __post_init__(self) -> None:
        normalized_id = _normalize_identifier(
            self.superseded_fact_id,
            field_name="superseded_fact_id",
        )
        object.__setattr__(self, "superseded_fact_id", normalized_id)
        correction_target = _validate_correction_fact(self.correction)
        if correction_target != normalized_id:
            raise ValueError(
                "Correction supersedes_fact_id must match the command target"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "superseded_fact_id": self.superseded_fact_id,
            "correction": self.correction.as_dict(),
        }


@dataclass(frozen=True)
class AppendOnlyCorrectionResult:
    """The retained old entry and the newly appended correction entry."""

    retained_fact: WorldFact
    appended_fact: WorldFact

    def __post_init__(self) -> None:
        if not isinstance(self.retained_fact, WorldFact):
            raise TypeError("retained_fact must be a WorldFact")
        correction_target = _validate_correction_fact(self.appended_fact)
        if correction_target != self.retained_fact.fact_id:
            raise ValueError("Appended correction must supersede retained_fact")
        if self.appended_fact.visibility != self.retained_fact.visibility:
            raise ValueError("Appended correction must preserve fact visibility")
        if self.appended_fact.pc_id != self.retained_fact.pc_id:
            raise ValueError("Appended correction must preserve the PC scope")
        if (
            self.appended_fact.subject != self.retained_fact.subject
            or self.appended_fact.predicate != self.retained_fact.predicate
        ):
            raise ValueError("Appended correction must preserve fact identity")

    def as_dict(self) -> dict[str, Any]:
        return {
            "append_only": True,
            "retained_fact": self.retained_fact.as_dict(),
            "appended_fact": self.appended_fact.as_dict(),
        }


__all__ = [
    "FACT_CATEGORIES",
    "FACT_VISIBILITIES",
    "AppendOnlyCorrectionCommand",
    "AppendOnlyCorrectionResult",
    "FactCategory",
    "FactVisibility",
    "WorldFact",
    "normalize_fact_text",
]
