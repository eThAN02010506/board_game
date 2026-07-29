"""Canonical typed declarative rule-object and citation models."""

from __future__ import annotations

from enum import Enum
from math import isfinite
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FIELD_PATH_PATTERN = (
    r"^[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*){0,15}$"
)
FieldPath = Annotated[
    str,
    Field(min_length=1, max_length=255, pattern=FIELD_PATH_PATTERN),
]


class ClosedRuleModel(BaseModel):
    """Reject fields outside the closed rule-object schema."""

    model_config = ConfigDict(extra="forbid")


class Audience(str, Enum):
    ALL = "all"
    PLAYER = "player"
    KP = "kp"


class RuleStatus(str, Enum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    REVIEW_REQUIRED = "review_required"
    QUARANTINED = "quarantined"


class Citation(ClosedRuleModel):
    chunk_id: str = Field(min_length=1)
    page: int = Field(ge=1)
    evidence_text: str = Field(min_length=2, max_length=500)
    evidence_hash: str | None = None


class Operand(ClosedRuleModel):
    value: float | int | str | bool | None = None
    field: FieldPath | None = None
    multiplier: float = Field(default=1.0, allow_inf_nan=False)
    divisor: float = Field(default=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def exactly_one_source(self) -> Operand:
        if (self.field is None) == (self.value is None):
            raise ValueError("operand requires exactly one of field or value")
        if self.divisor == 0:
            raise ValueError("operand divisor cannot be zero")
        if isinstance(self.value, float) and not isfinite(self.value):
            raise ValueError("operand value must be finite")
        return self


class Condition(ClosedRuleModel):
    left: Operand
    operator: Literal["eq", "ne", "lt", "lte", "gt", "gte", "in"]
    right: Operand


class Effect(ClosedRuleModel):
    target: FieldPath
    operation: Literal["set", "add", "subtract", "multiply", "min", "max"]
    operand: Operand


class RuleBranch(ClosedRuleModel):
    all: list[Condition] = Field(default_factory=list, max_length=64)
    effects: list[Effect] = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=200)


class LookupRow(ClosedRuleModel):
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)
    equals: str | int | float | bool | None = None
    output: dict[str, Any] = Field(max_length=128)

    @model_validator(mode="after")
    def has_matcher(self) -> LookupRow:
        if self.equals is None and self.minimum is None and self.maximum is None:
            raise ValueError("lookup row requires equals or a numeric range")
        if isinstance(self.equals, float) and not isfinite(self.equals):
            raise ValueError("lookup equality value must be finite")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("lookup row minimum cannot exceed maximum")
        return self


class RuleExecution(ClosedRuleModel):
    kind: Literal["reference_only", "condition_effects", "lookup_table"]
    inputs: list[FieldPath] = Field(default_factory=list, max_length=64)
    branches: list[RuleBranch] = Field(default_factory=list, max_length=128)
    lookup_input: FieldPath | None = None
    rows: list[LookupRow] = Field(default_factory=list, max_length=512)

    @model_validator(mode="after")
    def shape_matches_kind(self) -> RuleExecution:
        if self.kind == "condition_effects" and not self.branches:
            raise ValueError("condition_effects requires at least one branch")
        if self.kind == "condition_effects" and (self.lookup_input or self.rows):
            raise ValueError("condition_effects cannot contain lookup data")
        if self.kind == "lookup_table" and (not self.lookup_input or not self.rows):
            raise ValueError("lookup_table requires lookup_input and rows")
        if self.kind == "lookup_table" and self.branches:
            raise ValueError("lookup_table cannot contain executable branches")
        if self.kind == "reference_only" and (
            self.branches or self.lookup_input or self.rows
        ):
            raise ValueError("reference_only cannot contain executable data")
        operation_count = len(self.rows) + sum(
            len(branch.all) + len(branch.effects) for branch in self.branches
        )
        if operation_count > 512:
            raise ValueError("rule execution exceeds 512 operations")
        return self


class RuleObject(ClosedRuleModel):
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
    tags: list[str] = Field(default_factory=list, max_length=64)
    execution: RuleExecution
    citations: list[Citation] = Field(min_length=1, max_length=64)
    confidence: float = Field(ge=0, le=1)


class RuleGoldenCase(ClosedRuleModel):
    """A KP-authored deterministic example bound to one rule review."""

    name: str = Field(min_length=1, max_length=120)
    inputs: dict[str, Any] = Field(max_length=128)
    expected_output: dict[str, Any] = Field(max_length=128)


class RuleReviewSubmission(ClosedRuleModel):
    """Human decision required before an extracted rule can become executable."""

    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2000)
    golden_cases: list[RuleGoldenCase] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def require_review_evidence(self) -> RuleReviewSubmission:
        if self.decision == "approved" and not self.golden_cases:
            raise ValueError("Approved executable rules require at least one golden case")
        if self.decision == "rejected" and not (self.note or "").strip():
            raise ValueError("Rejected rules require a review note")
        return self


class RuleExtractionEnvelope(ClosedRuleModel):
    rules: list[RuleObject] = Field(default_factory=list, max_length=24)
