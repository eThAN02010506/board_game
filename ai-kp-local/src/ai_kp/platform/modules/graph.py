"""Narrow, provenance-bound module entity graph contracts."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_kp.platform.modules.knowledge import Visibility

EntityType = Literal[
    "npc",
    "location",
    "clue",
    "organization",
    "item",
    "event",
    "anchor",
]
RelationPredicate = Literal[
    "contains",
    "located_at",
    "knows",
    "owns",
    "member_of",
    "reveals",
    "leads_to",
    "provides_access_to",
    "blocks",
    "contradicts",
    "same_as",
    "involves",
]

TRAVERSABLE_PREDICATES = (
    "reveals",
    "leads_to",
    "provides_access_to",
    "same_as",
)
CONFLICT_PREDICATES = ("blocks", "contradicts")


class ModuleEntityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: EntityType
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    visibility: Visibility = "kp"
    spoiler_tag: str | None = Field(default=None, max_length=160)
    source_candidate_id: str


class ModuleRelationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_entity_id: str
    predicate: RelationPredicate
    target_entity_id: str
    source_candidate_id: str
    confidence: float = Field(default=1, ge=0, le=1)
    visibility: Visibility = "kp"
    spoiler_tag: str | None = Field(default=None, max_length=160)
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def distinct_endpoints(self) -> ModuleRelationCreate:
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("A relation requires two distinct entities")
        return self


def normalize_entity_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()
