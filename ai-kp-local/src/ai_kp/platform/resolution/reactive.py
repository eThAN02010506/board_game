"""Deterministic restricted behavior trees for NPCs, threats, and hazards."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.platform.resolution.contracts import (
    ReactiveRule,
    ScenarioContract,
    ScenarioSnapshot,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel

ReactiveTrigger = Literal[
    "after_action",
    "scene_entered",
    "clock_advanced",
    "background_tick",
    "semantic_event",
]


class ReactiveEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1, max_length=160)
    trigger: ReactiveTrigger


class ReactiveActivation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    entity_id: str
    rule_id: str
    rationale: str
    commands: tuple[WorldCommand, ...]


class ReactiveDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    run_id: str
    starting_run_version: int
    activations: tuple[ReactiveActivation, ...]
    resulting_snapshot: ScenarioSnapshot
    decision_hash: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"decision_hash"})


class ReactivePolicyEngine:
    """Run one priority selector per entity and preflight all selected commands."""

    def __init__(
        self,
        contract: ScenarioContract,
        *,
        kernel: ActionResolutionKernel | None = None,
    ):
        self.contract = contract
        # Settlement owns the single final ending evaluation. Reactive policy
        # selection must not terminate the run before sibling entities and
        # causally generated lifecycle events have settled.
        self.kernel = kernel or ActionResolutionKernel.from_contract(
            contract.model_copy(update={"endings": ()})
        )

    def evaluate(
        self, snapshot: ScenarioSnapshot, event: ReactiveEvent
    ) -> ReactiveDecision:
        if event.trigger == "semantic_event":
            raise ValueError(
                "Semantic events are authoritative TriggerRule inputs, not "
                "ReactivePolicy lifecycle events"
            )
        simulated = snapshot
        activations: list[ReactiveActivation] = []
        for policy in sorted(
            self.contract.reactive_policies, key=lambda item: item.policy_id
        ):
            selected = self._select_rule(simulated, event.trigger, policy.rules)
            if selected is None:
                continue
            activation = ReactiveActivation(
                policy_id=policy.policy_id,
                entity_id=policy.entity_id,
                rule_id=selected.rule_id,
                rationale=selected.rationale,
                commands=selected.commands,
            )
            simulated = self.kernel.preflight(simulated, selected.commands)
            activations.append(activation)
            if simulated.status == "completed":
                break
        draft = ReactiveDecision(
            event_id=event.event_id,
            run_id=snapshot.run_id,
            starting_run_version=snapshot.run_version,
            activations=tuple(activations),
            resulting_snapshot=simulated,
        )
        return draft.model_copy(
            update={"decision_hash": self._hash(draft.canonical_payload())}
        )

    def _select_rule(
        self,
        snapshot: ScenarioSnapshot,
        trigger: ReactiveTrigger,
        rules: tuple[ReactiveRule, ...],
    ) -> ReactiveRule | None:
        candidates = sorted(rules, key=lambda item: (-item.priority, item.rule_id))
        return next(
            (
                rule
                for rule in candidates
                if rule.trigger == trigger
                and self.kernel.conditions_satisfied(snapshot, rule.conditions)
            ),
            None,
        )

    @staticmethod
    def _hash(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ReactiveActivation",
    "ReactiveDecision",
    "ReactiveEvent",
    "ReactivePolicyEngine",
    "ReactiveTrigger",
]
