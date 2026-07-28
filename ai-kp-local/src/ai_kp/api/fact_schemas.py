"""Transport DTOs for the typed world-fact ledger."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AssertableFactType = Literal[
    "canonical_fact",
    "kp_secret",
    "character_belief",
    "rumor",
    "ai_hypothesis",
]
FactType = Literal[
    "canonical_fact",
    "kp_secret",
    "character_belief",
    "rumor",
    "ai_hypothesis",
    "retconned",
]


class WorldFactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_type: AssertableFactType
    subject: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=120)
    object_text: str = Field(min_length=1, max_length=4000)
    pc_id: str | None = Field(default=None, max_length=100)
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=20)
    source_reference: dict[str, Any] = Field(default_factory=dict)
    happened_at: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_character_scope(self) -> "WorldFactCreate":
        if self.fact_type == "character_belief" and self.pc_id is None:
            raise ValueError("character_belief requires pc_id")
        if self.fact_type != "character_belief" and self.pc_id is not None:
            raise ValueError("Only character_belief can target a PC")
        return self


class WorldFactRetcon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_head_event_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=4000)
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=20)
    source_reference: dict[str, Any] = Field(default_factory=dict)
    happened_at: str | None = Field(default=None, max_length=120)


__all__ = [
    "AssertableFactType",
    "FactType",
    "WorldFactCreate",
    "WorldFactRetcon",
]
