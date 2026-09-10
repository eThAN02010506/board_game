"""Small, lossy model-facing records used before strict contract assembly."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_kp.platform.resolution.contracts import (
    ConsequenceSignalBand,
    EntityCanonicalProfile,
    EntityDerivedProfile,
    OutcomeNarrativeCue,
    PlanStepSpec,
    PressureStage,
    ReactiveRule,
    ScenarioContract,
    SkillChoice,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.world_state_effect_ir import IrWorldStateEffect


class IrModel(BaseModel):
    # IR is a lossy model-facing transport. Unknown presentation metadata is
    # discarded; only declared fields can cross into the strict contract.
    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True)


class IrSourced(IrModel):
    source_block_ids: tuple[str, ...] = Field(min_length=1, max_length=8)


class IrLocation(IrSourced):
    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    visibility: Literal["hidden", "known", "visited"] = "hidden"
    tags: tuple[str, ...] = ()


class IrLocationLink(IrSourced):
    from_id: str = Field(min_length=1, max_length=160)
    to_id: str = Field(min_length=1, max_length=160)
    one_way: bool = False
    preconditions: tuple[StateCondition, ...] = ()


class IrEntity(IrSourced):
    id: str = Field(min_length=1, max_length=160)
    module_entity_id: str | None = Field(default=None, min_length=1, max_length=160)
    type: Literal[
        "npc", "creature", "item", "organization", "hazard", "event", "other"
    ]
    title: str = Field(min_length=1, max_length=240)
    status: str = Field(default="active", min_length=1, max_length=120)
    location_id: str | None = Field(default=None, max_length=160)
    canonical_profile: EntityCanonicalProfile = Field(
        default_factory=EntityCanonicalProfile
    )
    derived_profile: EntityDerivedProfile = Field(default_factory=EntityDerivedProfile)
    response_obligations: tuple[IrResponseObligation, ...] = Field(
        default=(), max_length=12
    )


class IrResponseObligation(IrModel):
    id: str = Field(min_length=1, max_length=160)
    trigger_topics: tuple[str, ...] = Field(default=(), max_length=16)
    conditions: tuple[StateCondition, ...] = ()
    facts_to_convey: tuple[str, ...] = Field(default=(), max_length=16)
    state_to_express: tuple[str, ...] = Field(default=(), max_length=12)
    physical_behaviors: tuple[str, ...] = Field(default=(), max_length=12)
    boundaries: tuple[str, ...] = Field(default=(), max_length=12)
    automatic_information: tuple[str, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def require_observable_content(self) -> IrResponseObligation:
        """Mirror the final contract invariant while record repair is still local."""

        if not any(
            (
                self.facts_to_convey,
                self.state_to_express,
                self.physical_behaviors,
                self.automatic_information,
            )
        ):
            raise ValueError("Response obligation must require observable content")
        return self


class IrClock(IrSourced):
    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    kind: Literal["soft", "hard"] = "hard"
    initial: int | float = Field(default=0, ge=0)
    maximum: int | float = Field(gt=0)
    pressure_visibility: Literal["table", "kp"] | None = None
    pressure_display_mode: Literal["exact", "stage", "narrative"] = "stage"
    pressure_stages: tuple[PressureStage, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_pressure(self) -> IrClock:
        if self.pressure_stages and self.pressure_visibility is None:
            raise ValueError("Pressure stages require pressure_visibility")
        if any(item.threshold > self.maximum for item in self.pressure_stages):
            raise ValueError("Pressure stage exceeds clock maximum")
        return self


class IrResource(IrSourced):
    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    initial: int | float = 0
    minimum: int | float = 0
    maximum: int | float | None = None


class IrClue(IrSourced):
    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    importance: Literal["core", "supporting", "optional"] = "supporting"
    discovery_action_ids: tuple[str, ...] = ()
    fact_path: str = Field(min_length=1, max_length=240)
    fact_value: Any = True
    recoverable: bool = True
    public_content: tuple[str, ...] = Field(default=(), max_length=12)


class IrAbstractCheck(IrModel):
    """Source-facing term resolved only through the installed ruleset catalog."""

    term: str = Field(min_length=1, max_length=120)
    difficulty: Literal["regular", "hard", "extreme"] = "regular"
    reason: str = Field(min_length=1, max_length=500)
    hidden: bool = False
    allow_push: bool = False
    failure_stakes: str = Field(default="", max_length=1000)
    pushed_failure_stakes: str = Field(default="", max_length=1000)


class ScenarioCheckMapping(IrModel):
    """Auditable result of resolving one source-facing check term."""

    action_id: str = Field(min_length=1, max_length=160)
    source_term: str = Field(min_length=1, max_length=120)
    resolved_skill_keys: tuple[str, ...] = Field(default=(), max_length=16)
    status: Literal["resolved", "unresolved"]


class IrAction(IrSourced):
    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    intent_hints: tuple[str, ...] = Field(default=(), max_length=12)
    public_setup: str = Field(default="", max_length=1200)
    narrative_cues: tuple[OutcomeNarrativeCue, ...] = Field(default=(), max_length=8)
    policy: Literal[
        "automatic",
        "choice",
        "required_check",
        "optional_check",
        "conditional_check",
        "opposed_check",
        "impossible",
        "clarification",
    ]
    # Model-facing scope is deliberately smaller than StateCondition.  A weak
    # model may select one opaque, partition-local location slot or copy one
    # exact declared location id; the server owns the executable condition.
    location_slot: int | None = Field(default=None, ge=0, le=11)
    location_id: str | None = Field(default=None, min_length=1, max_length=160)
    global_action: bool = False
    preconditions: tuple[StateCondition, ...] = ()
    checks: tuple[SkillChoice, ...] = ()
    automatic_information: tuple[str, ...] = Field(default=(), max_length=12)
    response_obligation_ids: tuple[str, ...] = Field(default=(), max_length=12)
    abstract_checks: tuple[IrAbstractCheck, ...] = Field(default=(), max_length=6)
    always: tuple[WorldCommand, ...] = Field(default=(), max_length=3)
    on_success: tuple[WorldCommand, ...] = Field(default=(), max_length=3)
    on_failure: tuple[WorldCommand, ...] = Field(default=(), max_length=3)
    on_pushed_failure: tuple[WorldCommand, ...] = Field(default=(), max_length=8)
    world_state_effects: tuple[IrWorldStateEffect, ...] = Field(default=(), max_length=8)
    rationale: str = Field(default="", max_length=1000)
    maximum_effect: str = Field(default="", max_length=1000)
    clarification_prompt: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def require_one_scope_selector(self) -> IrAction:
        selected = sum(
            (
                self.location_slot is not None,
                self.location_id is not None,
                self.global_action,
            )
        )
        if selected > 1:
            raise ValueError(
                "Action scope must select one location_slot, location_id, or global_action"
            )
        return self


class IrTaskMethod(IrSourced):
    id: str = Field(min_length=1, max_length=160)
    task_key: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    intent_hints: tuple[str, ...] = Field(default=(), max_length=12)
    preconditions: tuple[StateCondition, ...] = ()
    steps: tuple[PlanStepSpec, ...] = Field(min_length=1, max_length=8)


class IrReactivePolicy(IrSourced):
    id: str = Field(min_length=1, max_length=160)
    entity_id: str = Field(min_length=1, max_length=160)
    rules: tuple[ReactiveRule, ...] = Field(min_length=1, max_length=16)


class IrConsequenceSignal(IrSourced):
    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    visibility: Literal["table", "kp"] = "kp"
    display_mode: Literal["exact", "stage", "narrative"] = "stage"
    source_path: str | None = Field(default=None, min_length=1, max_length=240)
    public_title: str = Field(default="", max_length=240)
    public_summary: str = Field(default="", max_length=1000)
    bands: tuple[ConsequenceSignalBand, ...] = Field(min_length=1, max_length=8)


class IrEnding(IrSourced):
    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    priority: int = 0
    all_conditions: tuple[StateCondition, ...] = ()
    any_conditions: tuple[StateCondition, ...] = ()
    commands: tuple[WorldCommand, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def require_condition(self) -> IrEnding:
        # Keep the model-facing record aligned with the authoritative contract.
        # Otherwise an inert ending survives partition validation and can only
        # fail after all partitions have been assembled, where local repair is
        # no longer safe or evidence-bounded.
        if not self.all_conditions and not self.any_conditions:
            raise ValueError("An ending requires at least one condition")
        return self


class ScenarioIrBatch(IrModel):
    """One bounded source partition; identity and provenance remain server-owned."""

    confidence: Literal["low", "medium", "high"] = "medium"
    assumptions: tuple[str, ...] = Field(default=(), max_length=16)
    initial_scene_id: str | None = Field(default=None, max_length=160)
    initial_facts: dict[str, Any] = Field(default_factory=dict)
    locations: tuple[IrLocation, ...] = Field(default=(), max_length=12)
    location_links: tuple[IrLocationLink, ...] = Field(default=(), max_length=16)
    entities: tuple[IrEntity, ...] = Field(default=(), max_length=16)
    clocks: tuple[IrClock, ...] = Field(default=(), max_length=8)
    resources: tuple[IrResource, ...] = Field(default=(), max_length=8)
    clues: tuple[IrClue, ...] = Field(default=(), max_length=12)
    # A 16-block partition can legitimately contain more than eight compact
    # action paragraphs. Keep this local capacity below the merged contract
    # scale while avoiding collection-level failures that cannot be repaired
    # by record address.
    actions: tuple[IrAction, ...] = Field(default=(), max_length=16)
    task_methods: tuple[IrTaskMethod, ...] = Field(default=(), max_length=6)
    reactive_policies: tuple[IrReactivePolicy, ...] = Field(default=(), max_length=8)
    consequence_signals: tuple[IrConsequenceSignal, ...] = Field(
        default=(), max_length=8
    )
    endings: tuple[IrEnding, ...] = Field(default=(), max_length=8)


class ScenarioIrAssembly(IrModel):
    contract: ScenarioContract
    confidence: Literal["low", "medium", "high"]
    assumptions: tuple[str, ...] = Field(default=(), max_length=64)
    check_mappings: tuple[ScenarioCheckMapping, ...] = ()
    normalizations: tuple[ScenarioIrNormalization, ...] = ()


class ScenarioIrNormalization(IrModel):
    """A completed, authority-narrowing transform rather than an open assumption."""

    code: Literal[
        "kp_signal_public_projection_removed",
        "table_signal_downgraded",
        "exact_signal_precision_downgraded",
        "duplicate_signal_bands_removed",
    ]
    record_kind: Literal["consequence_signals"]
    record_id: str = Field(min_length=1, max_length=160)


__all__ = [
    "IrAbstractCheck",
    "IrAction",
    "IrClock",
    "IrClue",
    "IrConsequenceSignal",
    "IrEnding",
    "IrEntity",
    "IrLocation",
    "IrLocationLink",
    "IrReactivePolicy",
    "IrResource",
    "IrResponseObligation",
    "IrSourced",
    "IrTaskMethod",
    "ScenarioCheckMapping",
    "ScenarioIrAssembly",
    "ScenarioIrBatch",
    "ScenarioIrNormalization",
]
