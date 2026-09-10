"""Generic structured intent plan and deterministic policy validation."""

from __future__ import annotations

import re
from collections import Counter, deque
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    ConditionOperator,
    ScenarioContract,
    ScenarioSnapshot,
    StateCondition,
    TaskMethod,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel

ActionIntentStatus = Literal[
    "impossible", "possible_but_underspecified", "executable"
]
IntentAuditVerdict = Literal["accept", "clarification", "impossible"]
RequirementSource = Literal["state", "prior_step", "player_input", "unsupported"]
EffectRole = Literal["progress", "cost", "consequence"]
EffectOutcome = Literal["always", "success", "failure", "pushed_failure"]
DynamicCommandKind = Literal[
    "set_fact",
    "set_entity_status",
    "move_actor",
    "adjust_resource",
    "advance_clock",
    "set_scene",
    "emit_event",
    "apply_ruleset_effect",
]


class IntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class IntentRequirement(IntentModel):
    """One prerequisite and the only authority source allowed to satisfy it."""

    requirement_id: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    source: RequirementSource
    state_path: str | None = Field(default=None, max_length=240)
    comparison: ConditionOperator | None = None
    expected_value: Any = None
    producer_step_id: str | None = Field(default=None, max_length=120)
    produced_ref: str | None = Field(default=None, max_length=240)
    question: str = Field(default="", max_length=1000)
    player_evidence: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_source_binding(self) -> IntentRequirement:
        if self.source == "state" and (
            not self.state_path or self.comparison is None
        ):
            raise ValueError("state requirement needs state_path and comparison")
        if self.source == "state":
            StateCondition(
                path=str(self.state_path),
                operator=self.comparison,
                value=self.expected_value,
            )
        if self.source == "prior_step" and (
            not self.producer_step_id or not self.produced_ref
        ):
            raise ValueError("prior_step requirement needs producer_step_id and produced_ref")
        if self.source in {"player_input", "unsupported"} and not self.question:
            raise ValueError(f"{self.source} requirement needs a player-facing explanation")
        return self


class IntentEffectRef(IntentModel):
    """An authoritative effect that must be backed by a closed WorldCommand."""

    effect_id: str = Field(min_length=1, max_length=120)
    role: EffectRole
    applies_on: EffectOutcome
    command_kind: DynamicCommandKind
    target_ref: str = Field(min_length=1, max_length=240)
    value: Any = None
    delta: int | float | None = None
    actor_id: str | None = Field(default=None, max_length=160)
    payload: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(min_length=1, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def default_outcome_from_role(cls, value: Any) -> Any:
        """Keep older plans readable while making branch authority explicit."""

        if not isinstance(value, dict) or "applies_on" in value:
            return value
        normalized = dict(value)
        normalized["applies_on"] = {
            "cost": "always",
            "consequence": "failure",
            "progress": "success",
        }.get(normalized.get("role"), "success")
        return normalized

    @model_validator(mode="after")
    def validate_command_semantics(self) -> IntentEffectRef:
        """Require every state-changing parameter to be frozen by the intent."""

        value_kinds = {"set_fact", "set_entity_status"}
        delta_kinds = {"adjust_resource", "advance_clock"}
        payload_kinds = {"emit_event", "apply_ruleset_effect"}

        if self.command_kind in value_kinds and "value" not in self.model_fields_set:
            raise ValueError(f"{self.command_kind} intent effect requires value")
        if self.command_kind not in value_kinds and self.value is not None:
            raise ValueError(f"{self.command_kind} intent effect cannot declare value")
        if self.command_kind in delta_kinds and self.delta is None:
            raise ValueError(f"{self.command_kind} intent effect requires delta")
        if self.command_kind not in delta_kinds and self.delta is not None:
            raise ValueError(f"{self.command_kind} intent effect cannot declare delta")
        if self.command_kind == "move_actor" and not self.actor_id:
            raise ValueError("move_actor intent effect requires actor_id")
        if self.command_kind != "move_actor" and self.actor_id is not None:
            raise ValueError(f"{self.command_kind} intent effect cannot declare actor_id")
        if self.command_kind not in payload_kinds and self.payload:
            raise ValueError(f"{self.command_kind} intent effect cannot declare payload")
        return self


class IntentStep(IntentModel):
    """One semantic step bound to exactly one proposed primitive operator."""

    step_id: str = Field(min_length=1, max_length=120)
    operator_id: str = Field(min_length=1, max_length=160)
    goal: str = Field(min_length=1, max_length=500)
    method: str = Field(min_length=1, max_length=500)
    target: str = Field(min_length=1, max_length=300)
    depends_on: tuple[str, ...] = ()
    requirements: tuple[IntentRequirement, ...] = Field(default=(), max_length=12)
    effects: tuple[IntentEffectRef, ...] = Field(min_length=1, max_length=12)


class ActionIntentPlan(IntentModel):
    """Model-parsed semantics with no authority to mutate state by itself."""

    goal: str = Field(min_length=1, max_length=1000)
    steps: tuple[IntentStep, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_graph(self) -> ActionIntentPlan:
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("intent plan step IDs must be unique")
        known = set(step_ids)
        for step in self.steps:
            unknown = set(step.depends_on) - known
            if unknown:
                raise ValueError(
                    f"intent step {step.step_id} has unknown dependencies: {sorted(unknown)}"
                )
            if step.step_id in step.depends_on:
                raise ValueError(f"intent step {step.step_id} cannot depend on itself")
        if len(_topological_step_ids(self.steps)) != len(self.steps):
            raise ValueError("intent plan contains a dependency cycle")
        return self


class ActionIntentAssessment(IntentModel):
    status: ActionIntentStatus
    reason: str = Field(min_length=1, max_length=2000)
    questions: tuple[str, ...] = ()


class ActionIntentAudit(IntentModel):
    """Independent semantic review; it can reject but never rewrite a plan."""

    verdict: IntentAuditVerdict
    reason: str = Field(min_length=1, max_length=2000)
    issues: tuple[str, ...] = Field(default=(), max_length=12)
    questions: tuple[str, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def validate_verdict(self) -> ActionIntentAudit:
        if self.verdict == "accept" and (self.issues or self.questions):
            raise ValueError("accepted audit cannot retain issues or questions")
        if self.verdict == "clarification" and not self.questions:
            raise ValueError("clarification audit requires a player-facing question")
        if self.verdict == "impossible" and not self.issues:
            raise ValueError("impossible audit requires an authority issue")
        return self


class ActionIntentPolicyValidator:
    """Compute feasibility from declared authority bindings, never action vocabulary."""

    def assess(
        self,
        plan: ActionIntentPlan,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        *,
        operators: Sequence[ActionOperator],
        task_methods: Sequence[TaskMethod],
    ) -> ActionIntentAssessment:
        assessment = self.assess_requirements(plan, contract, snapshot)
        if assessment.status != "executable":
            return assessment
        self.validate_bindings(plan, operators, task_methods)
        return assessment

    def assess_requirements(
        self,
        plan: ActionIntentPlan,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        *,
        player_intent: str | None = None,
    ) -> ActionIntentAssessment:
        """Evaluate declared requirements before any executable records exist."""

        unsupported: list[str] = []
        questions: list[str] = []
        by_step = {step.step_id: step for step in plan.steps}
        ancestors = _dependency_ancestors(plan.steps)
        query = ActionResolutionKernel.from_contract(contract).query_snapshot(snapshot)

        for step in plan.steps:
            for requirement in step.requirements:
                if requirement.source == "unsupported":
                    unsupported.append(requirement.question)
                elif requirement.source == "player_input":
                    questions.append(requirement.question)
                elif requirement.source == "state":
                    assert requirement.state_path is not None
                    assert requirement.comparison is not None
                    expected_value = requirement.expected_value
                    found, current_value = query.state_value(requirement.state_path)
                    if (
                        found
                        and requirement.comparison in {"eq", "ne"}
                        and isinstance(current_value, (int, float))
                        and not isinstance(current_value, bool)
                        and isinstance(expected_value, str)
                    ):
                        try:
                            parsed = float(expected_value)
                            expected_value = int(parsed) if parsed.is_integer() else parsed
                        except ValueError:
                            pass
                    condition = StateCondition(
                        path=requirement.state_path,
                        operator=requirement.comparison,
                        value=expected_value,
                    )
                    if not query.condition_satisfied(condition):
                        questions.append(
                            requirement.question
                            or f"请说明如何满足：{requirement.description}"
                        )
                    elif player_intent is not None and _requires_player_grounding(
                        requirement.state_path
                    ) and not _state_reference_is_grounded(
                        requirement.state_path,
                        requirement.player_evidence,
                        player_intent,
                        contract,
                    ):
                        questions.append(
                            requirement.question
                            or f"请明确选择用于此步骤的已存在对象："
                            f"{requirement.description}"
                        )
                elif requirement.source == "prior_step":
                    producer_id = str(requirement.producer_step_id)
                    if producer_id not in ancestors[step.step_id]:
                        raise ValueError(
                            f"requirement {requirement.requirement_id} is not produced "
                            "by a dependency ancestor"
                        )
                    producer = by_step[producer_id]
                    produced_effects = tuple(
                        effect
                        for effect in producer.effects
                        if effect.target_ref == requirement.produced_ref
                    )
                    if not produced_effects:
                        raise ValueError(
                            f"requirement {requirement.requirement_id} references an "
                            "undeclared prior-step effect"
                        )
                    if not any(
                        effect.applies_on in {"always", "success"}
                        for effect in produced_effects
                    ):
                        raise ValueError(
                            f"requirement {requirement.requirement_id} relies on a "
                            "failure-only prior-step effect"
                        )

        if unsupported:
            return ActionIntentAssessment(
                status="impossible",
                reason=unsupported[0],
                questions=tuple(dict.fromkeys(unsupported)),
            )
        if questions:
            unique = tuple(dict.fromkeys(questions))
            return ActionIntentAssessment(
                status="possible_but_underspecified",
                reason=unique[0],
                questions=unique,
            )

        return ActionIntentAssessment(
            status="executable",
            reason="目标、步骤、前置条件和权威效果均可由当前契约表示。",
        )

    @classmethod
    def validate_bindings(
        cls,
        plan: ActionIntentPlan,
        operators: Sequence[ActionOperator],
        task_methods: Sequence[TaskMethod],
    ) -> None:
        planned_operator_ids = [step.operator_id for step in plan.steps]
        duplicate_planned = sorted(
            operator_id
            for operator_id, count in Counter(planned_operator_ids).items()
            if count > 1
        )
        if duplicate_planned:
            raise ValueError(
                "intent steps must bind unique operator IDs: "
                f"{', '.join(duplicate_planned)}"
            )
        authored_operator_ids = [operator.operator_id for operator in operators]
        duplicate_authored = sorted(
            operator_id
            for operator_id, count in Counter(authored_operator_ids).items()
            if count > 1
        )
        if duplicate_authored:
            raise ValueError(
                "proposed operators must have unique IDs: "
                f"{', '.join(duplicate_authored)}"
            )
        operator_by_id = {operator.operator_id: operator for operator in operators}
        expected_ids = set(planned_operator_ids)
        if len(operators) != len(plan.steps) or set(operator_by_id) != expected_ids:
            raise ValueError("intent steps and proposed operators must have a one-to-one binding")
        for step in plan.steps:
            operator = operator_by_id[step.operator_id]
            command_branches = _operator_command_branches(operator)
            if any(
                effect.applies_on == "pushed_failure" for effect in step.effects
            ) and not _operator_supports_push(operator):
                raise ValueError(
                    f"intent step {step.step_id} declares pushed_failure effects "
                    "without an actually pushable skill"
                )
            for effect in step.effects:
                if not any(
                    branch == effect.applies_on
                    and _command_matches_effect(command, effect)
                    for branch, command in command_branches
                ):
                    raise ValueError(
                        f"intent effect {effect.effect_id} has no matching authoritative "
                        f"command in {effect.applies_on} branch"
                    )
            for branch, command in command_branches:
                if not any(
                    effect.applies_on == branch
                    and _command_matches_effect(command, effect)
                    for effect in step.effects
                ):
                    raise ValueError(
                        f"operator {step.operator_id} contains an undeclared state "
                        f"command in {branch} branch"
                    )
        if len(plan.steps) == 1:
            return
        if len(task_methods) != 1:
            raise ValueError("multi-step intent requires exactly one task method")
        method = task_methods[0]
        planned = {
            step.step_id: (step.operator_id, set(step.depends_on)) for step in plan.steps
        }
        authored = {
            step.step_id: (step.operator_id, set(step.depends_on)) for step in method.steps
        }
        if authored != planned:
            raise ValueError("task method must preserve intent step bindings and dependencies")


def _topological_step_ids(steps: Sequence[IntentStep]) -> tuple[str, ...]:
    remaining = {step.step_id: set(step.depends_on) for step in steps}
    ready = deque(step.step_id for step in steps if not step.depends_on)
    completed: list[str] = []
    while ready:
        step_id = ready.popleft()
        if step_id in completed:
            continue
        completed.append(step_id)
        for candidate in steps:
            if (
                candidate.step_id not in completed
                and remaining[candidate.step_id] <= set(completed)
            ):
                ready.append(candidate.step_id)
    return tuple(completed)


def _dependency_ancestors(steps: Sequence[IntentStep]) -> dict[str, set[str]]:
    direct = {step.step_id: set(step.depends_on) for step in steps}
    result: dict[str, set[str]] = {}
    for step in steps:
        ancestors: set[str] = set()
        pending = list(direct[step.step_id])
        while pending:
            dependency = pending.pop()
            if dependency in ancestors:
                continue
            ancestors.add(dependency)
            pending.extend(direct[dependency])
        result[step.step_id] = ancestors
    return result


def _operator_command_branches(
    operator: ActionOperator,
) -> tuple[tuple[str, WorldCommand], ...]:
    return (
        *(("always", command) for command in operator.always_commands),
        *(("success", command) for command in operator.success_commands),
        *(("failure", command) for command in operator.failure_commands),
        *(
            (branch.outcome_key, command)
            for branch in operator.outcome_branches
            for command in branch.commands
        ),
    )


def _command_matches_effect(command: WorldCommand, effect: IntentEffectRef) -> bool:
    return command == world_command_for_intent_effect(effect)


def world_command_for_intent_effect(effect: IntentEffectRef) -> WorldCommand:
    """Project one frozen intent effect into its only authorized command."""

    common: dict[str, Any] = {
        "kind": effect.command_kind,
        "payload": effect.payload,
    }
    if effect.command_kind == "set_fact":
        common.update(path=effect.target_ref, value=effect.value)
    elif effect.command_kind == "set_entity_status":
        common.update(entity_id=effect.target_ref, value=effect.value)
    elif effect.command_kind == "move_actor":
        common.update(actor_id=effect.actor_id, value=effect.target_ref)
    elif effect.command_kind == "adjust_resource":
        common.update(path=effect.target_ref, delta=effect.delta)
    elif effect.command_kind == "advance_clock":
        common.update(clock_id=effect.target_ref, delta=effect.delta)
    elif effect.command_kind == "set_scene":
        common.update(value=effect.target_ref)
    elif effect.command_kind in {"emit_event", "apply_ruleset_effect"}:
        common.update(event_type=effect.target_ref)
    return WorldCommand.model_validate(common)


def _operator_supports_push(operator: ActionOperator) -> bool:
    return operator.policy in {
        "required_check",
        "optional_check",
        "conditional_check",
        "opposed_check",
    } and any(choice.allow_push for choice in operator.skill_choices)


_WORD = re.compile(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]+")


def _requires_player_grounding(path: str) -> bool:
    return path.partition(".")[0] in {
        "resources",
        "entities",
        "facts",
    }


def _state_reference_is_grounded(
    path: str,
    evidence: str,
    player_intent: str,
    contract: ScenarioContract,
) -> bool:
    """Require a verifiable player quote that identifies the chosen state reference."""

    quote = evidence.strip()
    if not quote or quote.casefold() not in player_intent.casefold():
        return False
    root, _, identifier = path.partition(".")
    labels = {identifier, identifier.replace("_", " ").replace("-", " ")}
    collections = {
        "resources": ((item.resource_id, item.title) for item in contract.resources),
        "clocks": ((item.clock_id, item.title) for item in contract.clocks),
        "entities": ((item.entity_id, item.title) for item in contract.entities),
    }
    for item_id, title in collections.get(root, ()):
        if item_id == identifier:
            labels.add(title)
    quote_tokens = {token.casefold() for token in _WORD.findall(quote)}
    label_tokens = {
        token.casefold() for label in labels for token in _WORD.findall(label)
    }
    return bool(quote_tokens & label_tokens) or any(
        label.casefold() in quote.casefold() for label in labels if label
    )


__all__ = [
    "ActionIntentAssessment",
    "ActionIntentAudit",
    "ActionIntentPlan",
    "ActionIntentPolicyValidator",
    "ActionIntentStatus",
    "EffectOutcome",
    "IntentEffectRef",
    "IntentRequirement",
    "IntentStep",
    "world_command_for_intent_effect",
]
