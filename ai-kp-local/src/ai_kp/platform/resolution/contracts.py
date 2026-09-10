"""Strict value objects shared by scenario compilation and action resolution."""

from __future__ import annotations

import re
from collections import Counter
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
    model_validator,
)

from ai_kp.platform.resolution.json_projection import validate_strict_json
from ai_kp.platform.resolution.narrative_safety import narration_claims_success

IntentHint = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
]

ResolutionPolicy = Literal[
    "automatic",
    "choice",
    "required_check",
    "optional_check",
    "conditional_check",
    "opposed_check",
    "impossible",
    "clarification",
]
ConditionOperator = Literal[
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "exists",
    "not_exists",
]
CommandKind = Literal[
    "set_fact",
    "remove_fact",
    "set_entity_status",
    "update_entity_runtime",
    "set_world_entity_state",
    "move_actor",
    "adjust_resource",
    "advance_clock",
    "set_scene",
    "complete_run",
    "emit_event",
    "apply_ruleset_effect",
    "activate_contract_overlay",
    "register_entity",
    "register_clock",
    "register_resource",
]


class KernelModel(BaseModel):
    """Strict immutable object crossing the authoritative kernel boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class SourceRef(KernelModel):
    """Stable provenance back to an extracted source block."""

    source_block_id: str = Field(min_length=1, max_length=160)
    document_id: str = Field(min_length=1, max_length=160)
    page: int | None = Field(default=None, ge=1)
    paragraph: int | None = Field(default=None, ge=0)


class StateCondition(KernelModel):
    """A closed-set comparison against a dotted snapshot path."""

    path: str = Field(min_length=1, max_length=240)
    operator: ConditionOperator
    value: Any = None

    @model_validator(mode="after")
    def validate_value(self) -> StateCondition:
        validate_strict_json(self.value, path="state_condition.value")
        if self.operator in {"gt", "gte", "lt", "lte"} and (
            not isinstance(self.value, (int, float)) or isinstance(self.value, bool)
        ):
            raise ValueError(f"{self.operator} requires a numeric value")
        return self


class SkillChoice(KernelModel):
    """Scenario-authorized check choice; target comes from the character sheet."""

    skill_key: str = Field(min_length=1, max_length=120)
    difficulty: Literal["regular", "hard", "extreme"] = "regular"
    reason: str = Field(min_length=1, max_length=500)
    hidden: bool = False
    bonus_dice: int = Field(default=0, ge=-2, le=2)
    allow_push: bool = True
    scope: str = Field(default="", max_length=500)
    supporting_factors: tuple[str, ...] = Field(default=(), max_length=8)
    automatic_information: tuple[str, ...] = Field(default=(), max_length=8)
    failure_stakes: str = Field(default="", max_length=1000)
    pushed_failure_stakes: str = Field(default="", max_length=1000)


class CheckPlan(KernelModel):
    """Player-reviewable check contract produced before any random resolution."""

    choices: tuple[SkillChoice, ...] = Field(min_length=1, max_length=8)
    selected_skill_key: str
    automatic_information: tuple[str, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def validate_selection(self) -> CheckPlan:
        keys = [item.skill_key for item in self.choices]
        if len(keys) != len(set(keys)):
            raise ValueError("Check plan skill choices must be unique")
        if self.selected_skill_key not in keys:
            raise ValueError("Check plan selection must be one of its choices")
        return self


class EntityCanonicalProfile(KernelModel):
    """Source-backed identity and knowledge that an actor model cannot rewrite."""

    summary: str = Field(default="", max_length=2000)
    known_facts: tuple[str, ...] = Field(default=(), max_length=32)
    secrets: tuple[str, ...] = Field(default=(), max_length=24)
    behavioral_directives: tuple[str, ...] = Field(default=(), max_length=16)
    boundaries: tuple[str, ...] = Field(default=(), max_length=16)


class EntityDerivedProfile(KernelModel):
    """Bounded characterization inferred from canon and replaceable on recompilation."""

    traits: tuple[str, ...] = Field(default=(), max_length=12)
    speech_style: tuple[str, ...] = Field(default=(), max_length=12)
    motivations: tuple[str, ...] = Field(default=(), max_length=12)
    fears: tuple[str, ...] = Field(default=(), max_length=12)
    mannerisms: tuple[str, ...] = Field(default=(), max_length=12)


class EntityRuntimeState(KernelModel):
    """Mutable per-run actor state kept outside the immutable source profile."""

    status: str = Field(default="active", min_length=1, max_length=120)
    location_id: str | None = Field(default=None, max_length=160)
    emotional_state: str = Field(default="", max_length=240)
    physical_state: str = Field(default="", max_length=240)
    attitude: str = Field(default="", max_length=240)
    short_term_goal: str = Field(default="", max_length=500)
    memory_refs: tuple[str, ...] = Field(default=(), max_length=32)


class ResponseObligation(KernelModel):
    """Facts and performance cues that an NPC response must visibly satisfy."""

    obligation_id: str = Field(min_length=1, max_length=160)
    entity_id: str = Field(min_length=1, max_length=160)
    trigger_topics: tuple[str, ...] = Field(default=(), max_length=16)
    conditions: tuple[StateCondition, ...] = ()
    facts_to_convey: tuple[str, ...] = Field(default=(), max_length=16)
    state_to_express: tuple[str, ...] = Field(default=(), max_length=12)
    physical_behaviors: tuple[str, ...] = Field(default=(), max_length=12)
    boundaries: tuple[str, ...] = Field(default=(), max_length=12)
    automatic_information: tuple[str, ...] = Field(default=(), max_length=12)
    source_refs: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def require_observable_content(self) -> ResponseObligation:
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


class WorldCommand(KernelModel):
    """Closed-set state mutation whose shape is validated by command kind."""

    kind: CommandKind
    path: str | None = Field(default=None, max_length=240)
    entity_id: str | None = Field(default=None, max_length=160)
    actor_id: str | None = Field(default=None, max_length=160)
    clock_id: str | None = Field(default=None, max_length=160)
    value: Any = None
    delta: int | float | None = None
    event_type: str | None = Field(default=None, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_shape(self) -> WorldCommand:
        validate_strict_json(self.value, path="world_command.value")
        validate_strict_json(self.payload, path="world_command.payload")
        identifier_fields = {
            "set_fact": "path",
            "remove_fact": "path",
            "set_entity_status": "entity_id",
            "update_entity_runtime": "entity_id",
            "set_world_entity_state": "entity_id",
            "move_actor": "actor_id",
            "adjust_resource": "path",
            "advance_clock": "clock_id",
            "emit_event": "event_type",
            "apply_ruleset_effect": "event_type",
            "register_entity": "entity_id",
            "register_clock": "clock_id",
            "register_resource": "path",
        }
        required = identifier_fields.get(self.kind)
        if required and not getattr(self, required):
            raise ValueError(f"{self.kind} requires {required}")
        if self.kind == "set_world_entity_state":
            if not self.path or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.path):
                raise ValueError("World entity state requires a stable dimension path")
            if set(self.payload) != {"visibility"} or self.payload["visibility"] not in {
                "table", "kp", "secret"
            }:
                raise ValueError("World entity state requires explicit visibility only")
            if self.value is not None and type(self.value) not in {str, int, bool}:
                raise ValueError("World entity state requires text, integer, boolean or null")
            if isinstance(self.value, str) and (not self.value.strip() or len(self.value) > 500):
                raise ValueError("World entity state text must contain 1..500 characters")
            if type(self.value) is int and not -(10**9) <= self.value <= 10**9:
                raise ValueError("World entity state number is outside supported range")
        if self.kind in {"adjust_resource", "advance_clock"} and self.delta is None:
            raise ValueError(f"{self.kind} requires delta")
        if self.kind in {"register_clock", "register_resource"} and (
            not isinstance(self.value, (int, float)) or isinstance(self.value, bool)
        ):
            raise ValueError(f"{self.kind} requires a numeric value")
        if self.kind == "activate_contract_overlay" and (
            not isinstance(self.value, str)
            or len(self.value) != 64
            or not isinstance(self.payload.get("source_version"), int)
        ):
            raise ValueError(
                "activate_contract_overlay requires a contract hash and source_version"
            )
        if self.kind in {
            "set_entity_status",
            "register_entity",
            "move_actor",
            "set_scene",
            "complete_run",
        } and (not isinstance(self.value, str) or not self.value.strip()):
            raise ValueError(f"{self.kind} requires value")
        if self.kind == "update_entity_runtime":
            allowed = {
                "emotional_state",
                "physical_state",
                "attitude",
                "short_term_goal",
                "add_memory_ref",
            }
            if not self.payload or set(self.payload) - allowed:
                raise ValueError("update_entity_runtime requires only supported runtime fields")
            if any(
                not isinstance(value, str) or not value.strip() for value in self.payload.values()
            ):
                raise ValueError("Entity runtime updates require non-empty strings")
        return self


class OutcomeBranch(KernelModel):
    """Commands for one exact, ruleset-returned outcome identifier."""

    outcome_key: str = Field(min_length=1, max_length=120)
    commands: tuple[WorldCommand, ...] = Field(min_length=1, max_length=16)


class OutcomeNarrativeCue(KernelModel):
    """Public, non-authoritative grounding for one verified action outcome."""

    outcome_key: str = Field(min_length=1, max_length=120)
    public_summary: str = Field(min_length=1, max_length=1200)
    speaker_entity_id: str | None = Field(default=None, max_length=160)
    tone: str = Field(default="", max_length=120)


class ActionIntent(KernelModel):
    """Normalized intent; semantic models select IDs but do not create authority."""

    action_id: str = Field(min_length=1, max_length=160)
    actor_id: str = Field(min_length=1, max_length=160)
    goal: str = Field(min_length=1, max_length=1000)
    method: str = Field(default="", max_length=1000)
    target_ids: tuple[str, ...] = ()
    operator_id: str | None = Field(default=None, max_length=160)
    requested_skill_key: str | None = Field(default=None, max_length=120)


class ScenarioSnapshot(KernelModel):
    """Minimal authoritative state consumed and returned by the pure reducer."""

    run_id: str = Field(min_length=1, max_length=160)
    contract_id: str = Field(default="unbound", min_length=1, max_length=160)
    scenario_version: int = Field(ge=1)
    run_version: int = Field(ge=0)
    status: Literal["active", "paused", "completed"] = "active"
    scene_id: str | None = Field(default=None, max_length=160)
    facts: dict[str, Any] = Field(default_factory=dict)
    entities: dict[str, str] = Field(default_factory=dict)
    entity_runtime: dict[str, EntityRuntimeState] = Field(default_factory=dict)
    actor_locations: dict[str, str] = Field(default_factory=dict)
    resources: dict[str, int | float] = Field(default_factory=dict)
    clocks: dict[str, int | float] = Field(default_factory=dict)
    events: tuple[dict[str, Any], ...] = ()
    ending_id: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_json_state(self) -> ScenarioSnapshot:
        validate_strict_json(self.facts, path="scenario_snapshot.facts")
        validate_strict_json(self.entities, path="scenario_snapshot.entities")
        validate_strict_json(
            self.actor_locations,
            path="scenario_snapshot.actor_locations",
        )
        validate_strict_json(self.resources, path="scenario_snapshot.resources")
        validate_strict_json(self.clocks, path="scenario_snapshot.clocks")
        for index, event in enumerate(self.events):
            validate_strict_json(
                event,
                path=f"scenario_snapshot.events[{index}]",
            )
        return self


class ActionOperator(KernelModel):
    """Executable primitive supplied by a versioned scenario contract."""

    operator_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    intent_hints: tuple[IntentHint, ...] = Field(default=(), max_length=12)
    public_setup: str = Field(default="", max_length=1200)
    narrative_cues: tuple[OutcomeNarrativeCue, ...] = Field(default=(), max_length=8)
    policy: ResolutionPolicy
    preconditions: tuple[StateCondition, ...] = ()
    skill_choices: tuple[SkillChoice, ...] = ()
    automatic_information: tuple[str, ...] = Field(default=(), max_length=12)
    response_obligation_ids: tuple[str, ...] = Field(default=(), max_length=12)
    always_commands: tuple[WorldCommand, ...] = ()
    success_commands: tuple[WorldCommand, ...] = ()
    failure_commands: tuple[WorldCommand, ...] = ()
    outcome_branches: tuple[OutcomeBranch, ...] = ()
    rationale: str = Field(default="", max_length=1000)
    maximum_effect: str = Field(default="", max_length=1000)
    clarification_prompt: str = Field(default="", max_length=1000)
    source_refs: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def validate_policy(self) -> ActionOperator:
        duplicate_hints = sorted(
            hint for hint, count in Counter(self.intent_hints).items() if count > 1
        )
        if duplicate_hints:
            raise ValueError(f"Duplicate operator intent hints: {duplicate_hints}")
        check_policies = {
            "required_check",
            "optional_check",
            "conditional_check",
            "opposed_check",
        }
        if self.policy in check_policies and not self.skill_choices:
            raise ValueError(f"{self.policy} requires at least one skill choice")
        if self.policy in {"impossible", "clarification"} and (
            self.always_commands or self.success_commands or self.failure_commands
        ):
            raise ValueError(f"{self.policy} cannot have settlement commands")
        outcome_keys = [item.outcome_key for item in self.outcome_branches]
        duplicates = sorted(key for key, count in Counter(outcome_keys).items() if count > 1)
        if duplicates:
            raise ValueError(f"Duplicate outcome keys: {', '.join(duplicates)}")
        if self.policy in {"impossible", "clarification"} and self.outcome_branches:
            raise ValueError(f"{self.policy} cannot have outcome branches")
        cue_keys = [item.outcome_key for item in self.narrative_cues]
        duplicate_cues = sorted(key for key, count in Counter(cue_keys).items() if count > 1)
        if duplicate_cues:
            raise ValueError(f"Duplicate narrative outcome cues: {duplicate_cues}")
        if self.narrative_cues and not self.public_setup.strip():
            raise ValueError("Narrative cues require a public_setup")
        if self.public_setup and narration_claims_success(self.public_setup):
            raise ValueError("public_setup cannot claim a completed success")
        allowed_cues = {"success", *(item.outcome_key for item in self.outcome_branches)}
        if self.policy in check_policies or self.failure_commands:
            allowed_cues.add("failure")
        unsupported_cues = sorted(set(cue_keys) - allowed_cues)
        if unsupported_cues:
            raise ValueError(f"Narrative cues use unsupported outcomes: {unsupported_cues}")
        failure_cue = next(
            (item for item in self.narrative_cues if item.outcome_key == "failure"),
            None,
        )
        if failure_cue and narration_claims_success(failure_cue.public_summary):
            raise ValueError("Failure narrative cues cannot claim success")
        return self


class ResolutionPreview(KernelModel):
    """Stable, reviewable result of resolution without committing it."""

    action_id: str
    run_id: str
    contract_id: str
    scenario_version: int
    run_version: int
    operator_id: str
    policy: ResolutionPolicy
    allowed: bool
    reason: str
    skill_choices: tuple[SkillChoice, ...] = ()
    selected_skill_key: str | None = None
    check_plan: CheckPlan | None = None
    automatic_information: tuple[str, ...] = ()
    response_obligation_ids: tuple[str, ...] = ()
    always_commands: tuple[WorldCommand, ...] = ()
    success_commands: tuple[WorldCommand, ...] = ()
    failure_commands: tuple[WorldCommand, ...] = ()
    outcome_branches: tuple[OutcomeBranch, ...] = ()
    maximum_effect: str = ""
    preview_hash: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude={"preview_hash"})
        # Keep pre-CheckPlan previews stable while hashing any new authoritative data.
        if self.check_plan is None:
            payload.pop("check_plan", None)
        if not self.automatic_information:
            payload.pop("automatic_information", None)
        if not self.response_obligation_ids:
            payload.pop("response_obligation_ids", None)
        return payload

    def commands_for_outcome(self, outcome_key: str) -> tuple[WorldCommand, ...]:
        """Resolve an exact branch, retaining binary compatibility for old contracts."""

        for branch in self.outcome_branches:
            if branch.outcome_key == outcome_key:
                return (*self.always_commands, *branch.commands)
        if outcome_key == "success":
            return (*self.always_commands, *self.success_commands)
        if outcome_key == "failure":
            return (*self.always_commands, *self.failure_commands)
        raise ValueError(f"Unsupported kernel outcome: {outcome_key}")


class LocationSpec(KernelModel):
    location_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    initial_visibility: Literal["hidden", "known", "visited"] = "hidden"
    tags: tuple[str, ...] = ()
    source_refs: tuple[SourceRef, ...] = ()


class LocationLink(KernelModel):
    from_location_id: str = Field(min_length=1, max_length=160)
    to_location_id: str = Field(min_length=1, max_length=160)
    one_way: bool = False
    preconditions: tuple[StateCondition, ...] = ()
    source_refs: tuple[SourceRef, ...] = ()


class EntitySpec(KernelModel):
    entity_id: str = Field(min_length=1, max_length=160)
    module_entity_id: str | None = Field(default=None, min_length=1, max_length=160)
    entity_type: Literal["npc", "creature", "item", "organization", "hazard", "event", "other"]
    title: str = Field(min_length=1, max_length=240)
    initial_status: str = Field(default="active", min_length=1, max_length=120)
    initial_location_id: str | None = Field(default=None, max_length=160)
    canonical_profile: EntityCanonicalProfile = Field(default_factory=EntityCanonicalProfile)
    derived_profile: EntityDerivedProfile = Field(default_factory=EntityDerivedProfile)
    initial_runtime: EntityRuntimeState | None = None
    source_refs: tuple[SourceRef, ...] = ()

    @model_serializer(mode="wrap")
    def serialize_identity(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        payload = handler(self)
        # Absent bindings must not change the hash of existing published contracts.
        if self.module_entity_id is None:
            payload.pop("module_entity_id", None)
        return payload

    @model_validator(mode="after")
    def validate_initial_runtime(self) -> EntitySpec:
        runtime = self.initial_runtime
        if runtime is not None:
            if runtime.status != self.initial_status:
                raise ValueError("Entity runtime status must match initial_status")
            if runtime.location_id != self.initial_location_id:
                raise ValueError("Entity runtime location must match initial_location_id")
        return self


class ClockSpec(KernelModel):
    clock_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    clock_kind: Literal["soft", "hard"] = "hard"
    initial_value: int | float = Field(default=0, ge=0)
    maximum_value: int | float = Field(gt=0)
    source_refs: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def validate_initial_value(self) -> ClockSpec:
        if self.initial_value > self.maximum_value:
            raise ValueError("Clock initial value cannot exceed its maximum")
        return self


class ResourceSpec(KernelModel):
    resource_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    initial_value: int | float = 0
    minimum_value: int | float = 0
    maximum_value: int | float | None = None
    source_refs: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def validate_bounds(self) -> ResourceSpec:
        if self.initial_value < self.minimum_value:
            raise ValueError("Resource initial value is below its minimum")
        if self.maximum_value is not None:
            if self.maximum_value < self.minimum_value:
                raise ValueError("Resource maximum is below its minimum")
            if self.initial_value > self.maximum_value:
                raise ValueError("Resource initial value exceeds its maximum")
        return self


class ClueSpec(KernelModel):
    clue_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    importance: Literal["core", "supporting", "optional"] = "supporting"
    discovery_operator_ids: tuple[str, ...] = ()
    fact_path: str = Field(min_length=1, max_length=240)
    fact_value: Any = True
    recoverable: bool = True
    public_content: tuple[str, ...] = Field(default=(), max_length=12)
    source_refs: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def validate_fact_value(self) -> ClueSpec:
        validate_strict_json(self.fact_value, path="clue.fact_value")
        return self


class EndingRule(KernelModel):
    ending_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    priority: int = 0
    all_conditions: tuple[StateCondition, ...] = ()
    any_conditions: tuple[StateCondition, ...] = ()
    commands: tuple[WorldCommand, ...] = ()
    source_refs: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def require_condition(self) -> EndingRule:
        if not self.all_conditions and not self.any_conditions:
            raise ValueError("An ending requires at least one condition")
        if any(command.kind == "complete_run" for command in self.commands):
            raise ValueError("The kernel appends complete_run for an ending")
        return self


class PlanStepSpec(KernelModel):
    step_id: str = Field(min_length=1, max_length=160)
    operator_id: str = Field(min_length=1, max_length=160)
    depends_on: tuple[str, ...] = ()
    actor_binding: str = Field(default="initiator", min_length=1, max_length=120)


class TaskMethod(KernelModel):
    """Bounded HTN method that decomposes one task into primitive operators."""

    method_id: str = Field(min_length=1, max_length=160)
    task_key: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    intent_hints: tuple[IntentHint, ...] = Field(default=(), max_length=12)
    preconditions: tuple[StateCondition, ...] = ()
    steps: tuple[PlanStepSpec, ...] = Field(min_length=1, max_length=8)
    source_refs: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def validate_steps(self) -> TaskMethod:
        duplicate_hints = sorted(
            hint for hint, count in Counter(self.intent_hints).items() if count > 1
        )
        if duplicate_hints:
            raise ValueError(f"Duplicate task method intent hints: {duplicate_hints}")
        step_ids = [step.step_id for step in self.steps]
        duplicates = sorted(key for key, count in Counter(step_ids).items() if count > 1)
        if duplicates:
            raise ValueError(f"Duplicate plan step ids: {', '.join(duplicates)}")
        known = set(step_ids)
        for step in self.steps:
            unknown = set(step.depends_on) - known
            if unknown:
                raise ValueError(
                    f"Plan step {step.step_id} has unknown dependencies: {sorted(unknown)}"
                )
            if step.step_id in step.depends_on:
                raise ValueError(f"Plan step {step.step_id} cannot depend on itself")
        return self


class ReactiveRule(KernelModel):
    """One condition/action sequence inside a deterministic priority selector."""

    rule_id: str = Field(min_length=1, max_length=160)
    trigger: Literal[
        "after_action",
        "scene_entered",
        "clock_advanced",
        "background_tick",
        "semantic_event",
    ]
    event_type: str | None = Field(default=None, max_length=120)
    target_entity_id: str | None = Field(default=None, max_length=160)
    priority: int = Field(default=0, ge=-1000, le=1000)
    conditions: tuple[StateCondition, ...] = ()
    commands: tuple[WorldCommand, ...] = Field(min_length=1, max_length=8)
    rationale: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_semantic_event(self) -> ReactiveRule:
        if self.trigger == "semantic_event" and not self.event_type:
            raise ValueError("Semantic reactive rules require event_type")
        if self.trigger != "semantic_event" and self.event_type is not None:
            raise ValueError("Only semantic reactive rules accept event_type")
        if self.trigger != "semantic_event" and self.target_entity_id is not None:
            raise ValueError("Only semantic reactive rules accept target_entity_id")
        return self


class ReactivePolicy(KernelModel):
    """Restricted behavior tree: first matching rule wins for one entity."""

    policy_id: str = Field(min_length=1, max_length=160)
    entity_id: str = Field(min_length=1, max_length=160)
    rules: tuple[ReactiveRule, ...] = Field(min_length=1, max_length=16)
    source_refs: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def validate_rules(self) -> ReactivePolicy:
        rule_ids = [item.rule_id for item in self.rules]
        duplicates = sorted(key for key, count in Counter(rule_ids).items() if count > 1)
        if duplicates:
            raise ValueError(f"Duplicate reactive rule ids: {', '.join(duplicates)}")
        return self


class TriggerRule(KernelModel):
    """Deterministic semantic-event rule evaluated independently of model narration."""

    trigger_id: str = Field(min_length=1, max_length=160)
    event_type: str = Field(min_length=1, max_length=120)
    target_entity_id: str | None = Field(default=None, max_length=160)
    priority: int = Field(default=0, ge=-1000, le=1000)
    mandatory: bool = True
    once_per_run: bool = True
    conditions: tuple[StateCondition, ...] = ()
    commands: tuple[WorldCommand, ...] = Field(min_length=1, max_length=16)
    rationale: str = Field(default="", max_length=1000)
    source_refs: tuple[SourceRef, ...] = ()


class PressureStage(KernelModel):
    stage_id: str = Field(min_length=1, max_length=160)
    threshold: int | float = Field(ge=0)
    public_label: str = Field(default="", max_length=240)
    public_description: str = Field(default="", max_length=1000)
    commands: tuple[WorldCommand, ...] = Field(default=(), max_length=12)


class PressureTrackSpec(KernelModel):
    """Generic escalating pressure backed by an authoritative scenario clock."""

    pressure_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    clock_id: str = Field(min_length=1, max_length=160)
    visibility: Literal["table", "kp"] = "table"
    display_mode: Literal["exact", "stage", "narrative"] = "stage"
    stages: tuple[PressureStage, ...] = Field(min_length=1, max_length=16)
    source_refs: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def validate_stages(self) -> PressureTrackSpec:
        ids = [item.stage_id for item in self.stages]
        if len(ids) != len(set(ids)):
            raise ValueError("Pressure stage ids must be unique")
        thresholds = [item.threshold for item in self.stages]
        if thresholds != sorted(thresholds) or len(thresholds) != len(set(thresholds)):
            raise ValueError("Pressure stage thresholds must be unique and ascending")
        if self.visibility == "table" and any(
            not item.public_label.strip() for item in self.stages
        ):
            raise ValueError("Table-visible pressure stages require public labels")
        return self


class ConsequenceSignalBand(KernelModel):
    """One deterministic presentation band for a committed world state."""

    band_id: str = Field(min_length=1, max_length=160)
    priority: int = Field(default=0, ge=-1000, le=1000)
    severity: int = Field(default=0, ge=0, le=4)
    all_conditions: tuple[StateCondition, ...] = Field(default=(), max_length=8)
    player_visible: bool = False
    public_label: str = Field(default="", max_length=240)
    public_description: str = Field(default="", max_length=1000)


class ConsequenceSignalSpec(KernelModel):
    """Visibility-safe projection of generic pressure or world consequences."""

    signal_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    visibility: Literal["table", "kp"] = "kp"
    display_mode: Literal["exact", "stage", "narrative"] = "stage"
    source_path: str | None = Field(default=None, min_length=1, max_length=240)
    public_title: str = Field(default="", max_length=240)
    public_summary: str = Field(default="", max_length=1000)
    bands: tuple[ConsequenceSignalBand, ...] = Field(min_length=1, max_length=8)
    source_refs: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def validate_disclosure(self) -> ConsequenceSignalSpec:
        band_ids = [item.band_id for item in self.bands]
        duplicates = sorted(key for key, count in Counter(band_ids).items() if count > 1)
        if duplicates:
            raise ValueError(f"Duplicate consequence signal bands: {', '.join(duplicates)}")
        if self.display_mode == "exact" and self.source_path is None:
            raise ValueError("Exact consequence signals require source_path")
        if self.visibility == "kp" and any(item.player_visible for item in self.bands):
            raise ValueError("KP-only consequence signals cannot have player-visible bands")
        if self.visibility == "table":
            if not self.public_title.strip():
                raise ValueError("Table-visible consequence signals require public_title")
            visible = [item for item in self.bands if item.player_visible]
            if not visible:
                raise ValueError("Table-visible consequence signals require a visible band")
            if any(not item.public_label.strip() for item in visible):
                raise ValueError("Player-visible consequence bands require public_label")
            allowed_roots = {"facts", "entities", "resources", "clocks", "scene_id"}
            paths = [
                *(item.path for band in self.bands for item in band.all_conditions),
                *([self.source_path] if self.source_path is not None else []),
            ]
            unsupported = sorted(
                path for path in paths if path.split(".", 1)[0] not in allowed_roots
            )
            if unsupported:
                raise ValueError(
                    "Table consequence signal uses private runtime metadata: "
                    + ", ".join(unsupported)
                )
            if self.display_mode == "exact" and self.source_path == "scene_id":
                raise ValueError("Exact table signals cannot expose an internal scene_id")
        return self


class ScenarioContract(KernelModel):
    """Versioned executable projection compiled from scenario source evidence."""

    contract_id: str = Field(min_length=1, max_length=160)
    schema_version: int = Field(default=1, ge=1)
    source_version: int = Field(ge=1)
    ruleset_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    initial_scene_id: str | None = Field(default=None, max_length=160)
    initial_facts: dict[str, Any] = Field(default_factory=dict)
    locations: tuple[LocationSpec, ...] = ()
    location_links: tuple[LocationLink, ...] = ()
    entities: tuple[EntitySpec, ...] = ()
    clocks: tuple[ClockSpec, ...] = ()
    resources: tuple[ResourceSpec, ...] = ()
    clues: tuple[ClueSpec, ...] = ()
    operators: tuple[ActionOperator, ...] = ()
    task_methods: tuple[TaskMethod, ...] = ()
    reactive_policies: tuple[ReactivePolicy, ...] = ()
    response_obligations: tuple[ResponseObligation, ...] = ()
    trigger_rules: tuple[TriggerRule, ...] = ()
    pressure_tracks: tuple[PressureTrackSpec, ...] = ()
    consequence_signals: tuple[ConsequenceSignalSpec, ...] = Field(default=(), max_length=64)
    endings: tuple[EndingRule, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> ScenarioContract:
        validate_strict_json(self.initial_facts, path="scenario_contract.initial_facts")
        collections = {
            "location": [item.location_id for item in self.locations],
            "entity": [item.entity_id for item in self.entities],
            "clock": [item.clock_id for item in self.clocks],
            "resource": [item.resource_id for item in self.resources],
            "clue": [item.clue_id for item in self.clues],
            "operator": [item.operator_id for item in self.operators],
            "method": [item.method_id for item in self.task_methods],
            "reactive policy": [item.policy_id for item in self.reactive_policies],
            "response obligation": [item.obligation_id for item in self.response_obligations],
            "trigger": [item.trigger_id for item in self.trigger_rules],
            "pressure track": [item.pressure_id for item in self.pressure_tracks],
            "consequence signal": [item.signal_id for item in self.consequence_signals],
            "ending": [item.ending_id for item in self.endings],
        }
        for label, identifiers in collections.items():
            duplicates = sorted(key for key, count in Counter(identifiers).items() if count > 1)
            if duplicates:
                raise ValueError(f"Duplicate {label} ids: {', '.join(duplicates)}")

        location_ids = set(collections["location"])
        operator_ids = set(collections["operator"])
        clock_ids = set(collections["clock"])
        resource_ids = set(collections["resource"])
        entity_ids = set(collections["entity"])
        if self.initial_scene_id is not None and self.initial_scene_id not in location_ids:
            raise ValueError(f"Unknown initial scene: {self.initial_scene_id}")
        for link in self.location_links:
            if link.from_location_id not in location_ids or link.to_location_id not in location_ids:
                raise ValueError("Location link references an unknown location")
        for entity in self.entities:
            if entity.initial_location_id and entity.initial_location_id not in location_ids:
                raise ValueError(f"Entity {entity.entity_id} has an unknown initial location")
        for clue in self.clues:
            unknown = set(clue.discovery_operator_ids) - operator_ids
            if unknown:
                raise ValueError(
                    f"Clue {clue.clue_id} references unknown operators: {sorted(unknown)}"
                )
            if (
                clue.importance == "core"
                and not clue.discovery_operator_ids
                and not self._mapping_has_path(self.initial_facts, clue.fact_path)
            ):
                raise ValueError(f"Core clue {clue.clue_id} has no discovery route or initial fact")
        for operator in self.operators:
            unknown_obligations = set(operator.response_obligation_ids) - set(
                collections["response obligation"]
            )
            if unknown_obligations:
                raise ValueError(
                    f"Operator {operator.operator_id} references unknown response "
                    f"obligations: {sorted(unknown_obligations)}"
                )
            commands = [
                *operator.always_commands,
                *operator.success_commands,
                *operator.failure_commands,
            ]
            commands.extend(
                command for branch in operator.outcome_branches for command in branch.commands
            )
            for command in commands:
                self._validate_command_ref(
                    command, location_ids, entity_ids, clock_ids, resource_ids
                )
        for method in self.task_methods:
            unknown = {step.operator_id for step in method.steps} - operator_ids
            if unknown:
                raise ValueError(
                    f"Task method {method.method_id} references unknown operators: "
                    f"{sorted(unknown)}"
                )
            self._validate_method_acyclic(method)
        for policy in self.reactive_policies:
            if policy.entity_id not in entity_ids:
                raise ValueError(
                    f"Reactive policy {policy.policy_id} references unknown entity: "
                    f"{policy.entity_id}"
                )
            for rule in policy.rules:
                if rule.target_entity_id and rule.target_entity_id not in entity_ids:
                    raise ValueError(
                        f"Reactive rule {rule.rule_id} references unknown entity: "
                        f"{rule.target_entity_id}"
                    )
                for command in rule.commands:
                    self._validate_command_ref(
                        command, location_ids, entity_ids, clock_ids, resource_ids
                    )
        for obligation in self.response_obligations:
            if obligation.entity_id not in entity_ids:
                raise ValueError(
                    f"Response obligation {obligation.obligation_id} references "
                    f"unknown entity: {obligation.entity_id}"
                )
        for trigger in self.trigger_rules:
            if trigger.target_entity_id and trigger.target_entity_id not in entity_ids:
                raise ValueError(
                    f"Trigger {trigger.trigger_id} references unknown entity: "
                    f"{trigger.target_entity_id}"
                )
            for command in trigger.commands:
                self._validate_command_ref(
                    command, location_ids, entity_ids, clock_ids, resource_ids
                )
        clock_maxima = {item.clock_id: item.maximum_value for item in self.clocks}
        for pressure in self.pressure_tracks:
            if pressure.clock_id not in clock_ids:
                raise ValueError(
                    f"Pressure track {pressure.pressure_id} references unknown clock: "
                    f"{pressure.clock_id}"
                )
            if pressure.stages[-1].threshold > clock_maxima[pressure.clock_id]:
                raise ValueError(f"Pressure track {pressure.pressure_id} exceeds its clock maximum")
            for stage in pressure.stages:
                for command in stage.commands:
                    self._validate_command_ref(
                        command, location_ids, entity_ids, clock_ids, resource_ids
                    )
        for ending in self.endings:
            for command in ending.commands:
                self._validate_command_ref(
                    command, location_ids, entity_ids, clock_ids, resource_ids
                )
        return self

    @staticmethod
    def _validate_method_acyclic(method: TaskMethod) -> None:
        dependencies = {step.step_id: set(step.depends_on) for step in method.steps}
        completed: set[str] = set()
        while len(completed) < len(dependencies):
            ready = {
                step_id
                for step_id, required in dependencies.items()
                if step_id not in completed and required <= completed
            }
            if not ready:
                raise ValueError(f"Task method {method.method_id} contains a dependency cycle")
            completed.update(ready)

    def _validate_command_ref(
        self,
        command: WorldCommand,
        location_ids: set[str],
        entity_ids: set[str],
        clock_ids: set[str],
        resource_ids: set[str],
    ) -> None:
        if command.kind in {
            "activate_contract_overlay",
            "register_entity",
            "register_clock",
            "register_resource",
        }:
            raise ValueError(f"Contract operators cannot use control command: {command.kind}")
        if command.kind in {"set_scene", "move_actor"} and command.value not in location_ids:
            raise ValueError(f"Command references unknown location: {command.value}")
        if command.kind in {"set_entity_status", "update_entity_runtime"} and (
            command.entity_id not in entity_ids
        ):
            raise ValueError(f"Command references unknown entity: {command.entity_id}")
        if command.kind == "set_world_entity_state" and not any(
            item.entity_id == command.entity_id and item.module_entity_id
            for item in self.entities
        ):
            raise ValueError("World entity state requires an explicit module entity identity")
        if command.kind == "advance_clock" and command.clock_id not in clock_ids:
            raise ValueError(f"Command references unknown clock: {command.clock_id}")
        if command.kind == "adjust_resource" and command.path not in resource_ids:
            raise ValueError(f"Command references unknown resource: {command.path}")

    @staticmethod
    def _mapping_has_path(root: dict[str, Any], path: str) -> bool:
        current: Any = root
        normalized = path.removeprefix("facts.")
        for part in normalized.split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        return True

    def initial_snapshot(
        self, run_id: str, *, actor_locations: dict[str, str] | None = None
    ) -> ScenarioSnapshot:
        locations = dict(actor_locations or {})
        unknown_locations = set(locations.values()) - {item.location_id for item in self.locations}
        if unknown_locations:
            raise ValueError(f"Actors reference unknown locations: {sorted(unknown_locations)}")
        return ScenarioSnapshot(
            run_id=run_id,
            contract_id=self.contract_id,
            scenario_version=self.source_version,
            run_version=0,
            scene_id=self.initial_scene_id,
            facts=self.initial_facts,
            entities={item.entity_id: item.initial_status for item in self.entities},
            entity_runtime={
                item.entity_id: (
                    item.initial_runtime
                    or EntityRuntimeState(
                        status=item.initial_status,
                        location_id=item.initial_location_id,
                    )
                )
                for item in self.entities
            },
            actor_locations=locations,
            resources={item.resource_id: item.initial_value for item in self.resources},
            clocks={item.clock_id: item.initial_value for item in self.clocks},
        )


__all__ = [
    "ActionIntent",
    "ActionOperator",
    "CheckPlan",
    "ClockSpec",
    "ClueSpec",
    "ConsequenceSignalBand",
    "ConsequenceSignalSpec",
    "EndingRule",
    "EntityCanonicalProfile",
    "EntityDerivedProfile",
    "EntityRuntimeState",
    "EntitySpec",
    "LocationLink",
    "LocationSpec",
    "OutcomeBranch",
    "OutcomeNarrativeCue",
    "PlanStepSpec",
    "PressureStage",
    "PressureTrackSpec",
    "ReactivePolicy",
    "ReactiveRule",
    "ResolutionPolicy",
    "ResolutionPreview",
    "ResourceSpec",
    "ResponseObligation",
    "ScenarioContract",
    "ScenarioSnapshot",
    "SkillChoice",
    "SourceRef",
    "StateCondition",
    "TaskMethod",
    "TriggerRule",
    "WorldCommand",
]
