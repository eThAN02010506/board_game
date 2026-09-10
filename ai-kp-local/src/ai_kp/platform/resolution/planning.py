"""Bounded HTN-style planning over authoritative primitive action operators."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.platform.resolution.contracts import (
    ActionIntent,
    PlanStepSpec,
    ResolutionPreview,
    ScenarioContract,
    ScenarioSnapshot,
    TaskMethod,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str = Field(min_length=1, max_length=160)
    action_id: str = Field(min_length=1, max_length=160)
    actor_id: str = Field(min_length=1, max_length=160)
    task_key: str = Field(min_length=1, max_length=160)
    method_id: str = Field(min_length=1, max_length=160)
    actor_bindings: dict[str, str] = Field(default_factory=dict)
    requested_skill_keys: dict[str, str] = Field(default_factory=dict)


class PlannedStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str
    actor_id: str
    preview: ResolutionPreview
    assumed_outcome: Literal["success"] = "success"


class BoundedPlanPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    run_id: str
    contract_id: str
    scenario_version: int
    starting_run_version: int
    method_id: str
    steps: tuple[PlannedStep, ...]
    check_step_ids: tuple[str, ...]
    completes_scenario: bool
    resulting_snapshot: ScenarioSnapshot
    preview_hash: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"preview_hash"})


class BoundedTaskPlanner:
    """Decompose one selected method and simulate its success path without writes."""

    MAX_STEPS = 8

    def __init__(self, contract: ScenarioContract):
        self.contract = contract
        self.kernel = ActionResolutionKernel.from_contract(contract)
        self._methods = {item.method_id: item for item in contract.task_methods}

    def preview(self, snapshot: ScenarioSnapshot, request: PlanRequest) -> BoundedPlanPreview:
        method = self._methods.get(request.method_id)
        if method is None:
            raise KeyError(f"Unknown task method: {request.method_id}")
        if method.task_key != request.task_key:
            raise ValueError("Selected method does not implement the requested task")
        if len(method.steps) > self.MAX_STEPS:
            raise ValueError("Plan exceeds the bounded primitive-step limit")
        if not self.kernel.conditions_satisfied(snapshot, method.preconditions):
            raise ValueError("Task method preconditions are not satisfied")

        ordered = self._topological_steps(method)
        simulated = snapshot
        planned: list[PlannedStep] = []
        check_steps: list[str] = []
        for index, step in enumerate(ordered):
            if simulated.status == "completed":
                raise ValueError(
                    f"Plan reaches a terminal state before required step {step.step_id}"
                )
            actor_id = self._resolve_actor(request, step.actor_binding)
            preview = self.kernel.preview(
                simulated,
                ActionIntent(
                    action_id=f"{request.action_id}:{index}:{step.step_id}",
                    actor_id=actor_id,
                    goal=request.task_key,
                    operator_id=step.operator_id,
                    requested_skill_key=request.requested_skill_keys.get(step.step_id),
                ),
            )
            if not preview.allowed:
                raise ValueError(
                    f"Plan step {step.step_id} is blocked: {preview.reason}"
                )
            planned.append(
                PlannedStep(step_id=step.step_id, actor_id=actor_id, preview=preview)
            )
            if preview.skill_choices:
                check_steps.append(step.step_id)
            simulated = self.kernel.preflight(
                simulated, preview.commands_for_outcome("success")
            )

        draft = BoundedPlanPreview(
            plan_id=request.plan_id,
            run_id=snapshot.run_id,
            contract_id=snapshot.contract_id,
            scenario_version=snapshot.scenario_version,
            starting_run_version=snapshot.run_version,
            method_id=method.method_id,
            steps=tuple(planned),
            check_step_ids=tuple(check_steps),
            completes_scenario=simulated.status == "completed",
            resulting_snapshot=simulated,
        )
        return draft.model_copy(update={"preview_hash": self._hash(draft.canonical_payload())})

    @staticmethod
    def _resolve_actor(request: PlanRequest, binding: str) -> str:
        if binding == "initiator":
            return request.actor_id
        actor_id = request.actor_bindings.get(binding)
        if not actor_id:
            raise ValueError(f"Plan requires actor binding: {binding}")
        return actor_id

    @staticmethod
    def _topological_steps(method: TaskMethod) -> list[PlanStepSpec]:
        by_id = {item.step_id: item for item in method.steps}
        remaining = {item.step_id: set(item.depends_on) for item in method.steps}
        ordered: list[PlanStepSpec] = []
        completed: set[str] = set()
        ready = deque(
            item.step_id for item in method.steps if not remaining[item.step_id]
        )
        while ready:
            step_id = ready.popleft()
            if step_id in completed:
                continue
            ordered.append(by_id[step_id])
            completed.add(step_id)
            for candidate in method.steps:
                if candidate.step_id not in completed and remaining[candidate.step_id] <= completed:
                    ready.append(candidate.step_id)
        if len(ordered) != len(method.steps):
            raise ValueError("Task method contains a dependency cycle")
        return ordered

    @staticmethod
    def _hash(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = ["BoundedPlanPreview", "BoundedTaskPlanner", "PlanRequest", "PlannedStep"]
