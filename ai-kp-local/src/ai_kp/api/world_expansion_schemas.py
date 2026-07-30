"""Strict HTTP contracts for confirming world-expansion contact."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EncounterFactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_type: Literal["canonical_fact", "kp_secret", "rumor"] = "canonical_fact"
    subject: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=120)
    object_text: str = Field(min_length=1, max_length=4000)


class EncounterNpcInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, max_length=200)
    home_location: str | None = Field(default=None, max_length=300)
    profession: str | None = Field(default=None, max_length=200)
    public_notes: str = Field(default="", max_length=4000)
    secret_notes: str = Field(default="", max_length=8000)
    role: str = Field(default="encountered", min_length=1, max_length=120)
    relationship_score: int = Field(default=0, ge=-100, le=100)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def require_existing_or_new_npc(self) -> "EncounterNpcInput":
        has_id = bool(self.npc_id and self.npc_id.strip())
        has_name = bool(self.name and self.name.strip())
        if has_id == has_name:
            raise ValueError("Provide exactly one of npc_id or name")
        return self


class EncounterMapPlacementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    map_id: str = Field(min_length=1, max_length=100)
    location_name: str = Field(min_length=1, max_length=300)
    visibility: Literal["table", "kp"] = "table"
    color: str = Field(
        default="#b93f2d",
        pattern=r"^#[0-9A-Fa-f]{6}$",
    )


class WorldExpansionEncounterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    summary: str = Field(min_length=1, max_length=2000)
    happened_at: str | None = Field(default=None, max_length=120)
    facts: list[EncounterFactInput] = Field(min_length=1, max_length=8)
    npc: EncounterNpcInput | None = None
    map_placement: EncounterMapPlacementInput | None = None
    participant_investigator_ids: list[str] = Field(
        default_factory=list,
        max_length=12,
    )
    interaction_summary: str | None = Field(default=None, max_length=1000)
    profession_context: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def map_requires_npc(self) -> "WorldExpansionEncounterRequest":
        if self.map_placement is not None and self.npc is None:
            raise ValueError("map_placement requires npc")
        if self.participant_investigator_ids and self.npc is None:
            raise ValueError("participant_investigator_ids requires npc")
        normalized_ids = [item.strip() for item in self.participant_investigator_ids]
        if any(not item or len(item) > 100 for item in normalized_ids):
            raise ValueError("participant investigator IDs must contain 1-100 characters")
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("participant investigator IDs must be unique")
        self.participant_investigator_ids = normalized_ids
        return self


__all__ = [
    "EncounterFactInput",
    "EncounterMapPlacementInput",
    "EncounterNpcInput",
    "WorldExpansionEncounterRequest",
]
