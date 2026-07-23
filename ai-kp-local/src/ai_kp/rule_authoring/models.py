"""Canonical typed declarative rule-object and citation models."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Audience(str, Enum):
    ALL = "all"
    PLAYER = "player"
    KP = "kp"


class RuleStatus(str, Enum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    REVIEW_REQUIRED = "review_required"
    QUARANTINED = "quarantined"


class Citation(BaseModel):
    chunk_id: str = Field(min_length=1)
    page: int = Field(ge=1)
    evidence_text: str = Field(min_length=2, max_length=500)
    evidence_hash: str | None = None


class Operand(BaseModel):
    value: float | int | str | bool | None = None
    field: str | None = None
    multiplier: float = 1.0
    divisor: float = 1.0

    @model_validator(mode="after")
    def exactly_one_source(self) -> "Operand":
        if (self.field is None) == (self.value is None):
            raise ValueError("operand requires exactly one of field or value")
        if self.divisor == 0:
            raise ValueError("operand divisor cannot be zero")
        return self


class Condition(BaseModel):
    left: Operand
    operator: Literal["eq", "ne", "lt", "lte", "gt", "gte", "in"]
    right: Operand


class Effect(BaseModel):
    target: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_.]*$")
    operation: Literal["set", "add", "subtract", "multiply", "min", "max"]
    operand: Operand


class RuleBranch(BaseModel):
    all: list[Condition] = Field(default_factory=list)
    effects: list[Effect] = Field(min_length=1)
    label: str | None = None


class LookupRow(BaseModel):
    minimum: float | None = None
    maximum: float | None = None
    equals: str | int | float | bool | None = None
    output: dict[str, Any]

    @model_validator(mode="after")
    def has_matcher(self) -> "LookupRow":
        if self.equals is None and self.minimum is None and self.maximum is None:
            raise ValueError("lookup row requires equals or a numeric range")
        return self


class RuleExecution(BaseModel):
    kind: Literal["reference_only", "condition_effects", "lookup_table"]
    inputs: list[str] = Field(default_factory=list)
    branches: list[RuleBranch] = Field(default_factory=list)
    lookup_input: str | None = None
    rows: list[LookupRow] = Field(default_factory=list)

    @model_validator(mode="after")
    def shape_matches_kind(self) -> "RuleExecution":
        if self.kind == "condition_effects" and not self.branches:
            raise ValueError("condition_effects requires at least one branch")
        if self.kind == "lookup_table" and (not self.lookup_input or not self.rows):
            raise ValueError("lookup_table requires lookup_input and rows")
        if self.kind == "reference_only" and (self.branches or self.rows):
            raise ValueError("reference_only cannot contain executable branches or rows")
        return self


class RuleObject(BaseModel):
    rule_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,159}$")
    ruleset_id: str = "coc7-keeper-cn-2002c"
    title: str = Field(min_length=1, max_length=200)
    rule_type: Literal[
        "check",
        "combat",
        "damage",
        "healing",
        "sanity",
        "chase",
        "character",
        "magic",
        "creature",
        "keeper_guidance",
        "other",
    ] = "other"
    summary: str = Field(min_length=1, max_length=1500)
    audience: Audience = Audience.ALL
    tags: list[str] = Field(default_factory=list)
    execution: RuleExecution
    citations: list[Citation] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class RuleExtractionEnvelope(BaseModel):
    rules: list[RuleObject] = Field(default_factory=list, max_length=24)
