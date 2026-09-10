"""Atomic deterministic settlement for simultaneous player actions."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_kp.platform.resolution.command_writes import exclusive_assignments
from ai_kp.platform.resolution.contracts import (
    ActionIntent,
    ResolutionPreview,
    ScenarioContract,
    ScenarioSnapshot,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.settlement import resolved_action_commands

ParallelSettlementStatus = Literal["ready", "awaiting_checks", "conflict", "blocked"]


class ParallelIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(min_length=1, max_length=160)
    actor_id: str = Field(min_length=1, max_length=160)
    operator_id: str = Field(min_length=1, max_length=160)
    goal: str = Field(default="simultaneous action", min_length=1, max_length=1000)
    requested_skill_key: str | None = Field(default=None, max_length=120)
    # Outcome identifiers belong to the installed ruleset/compiled operator.
    # Keeping this open avoids collapsing exact branches such as
    # ``pushed_failure`` into a lossy binary result.
    outcome: str | None = Field(default=None, min_length=1, max_length=120)
    priority: int = Field(default=0, ge=-1000, le=1000)


class ParallelSettlementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str = Field(min_length=1, max_length=160)
    intents: tuple[ParallelIntent, ...] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def unique_actions_and_actors(self) -> ParallelSettlementRequest:
        action_ids = [item.action_id for item in self.intents]
        actor_ids = [item.actor_id for item in self.intents]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("Parallel action IDs must be unique")
        if len(actor_ids) != len(set(actor_ids)):
            raise ValueError("One actor can submit only one action per parallel batch")
        return self


class ParallelResolvedAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    actor_id: str
    priority: int
    outcome: str | None = Field(default=None, min_length=1, max_length=120)
    preview: ResolutionPreview
    commands: tuple[WorldCommand, ...] = ()


class ParallelSettlementPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str
    run_id: str
    starting_run_version: int
    status: ParallelSettlementStatus
    actions: tuple[ParallelResolvedAction, ...]
    conflicts: tuple[str, ...] = ()
    commands: tuple[WorldCommand, ...] = ()
    resulting_snapshot: ScenarioSnapshot
    request_hash: str = Field(default="", max_length=64)
    settlement_hash: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"settlement_hash"})


class ParallelActionKernel:
    """Evaluate against one snapshot and preflight one combined command batch."""

    def __init__(self, contract: ScenarioContract):
        self.contract = contract
        self.kernel = ActionResolutionKernel.from_contract(contract)

    def preview(
        self,
        snapshot: ScenarioSnapshot,
        request: ParallelSettlementRequest,
    ) -> ParallelSettlementPreview:
        ordered = sorted(
            request.intents, key=lambda item: (-item.priority, item.action_id)
        )
        resolved: list[ParallelResolvedAction] = []
        awaiting_checks = False
        blocked: list[str] = []
        for item in ordered:
            preview = self.kernel.preview(
                snapshot,
                ActionIntent(
                    action_id=item.action_id,
                    actor_id=item.actor_id,
                    goal=item.goal,
                    operator_id=item.operator_id,
                    requested_skill_key=item.requested_skill_key,
                ),
            )
            if not preview.allowed:
                blocked.append(f"{item.action_id}:{preview.reason}")
            outcome = item.outcome
            if preview.skill_choices and outcome is None:
                awaiting_checks = True
            if not preview.skill_choices and outcome is None:
                outcome = "success"
            commands: tuple[WorldCommand, ...] = ()
            if preview.allowed:
                try:
                    commands = self._commands_for(preview, outcome)
                except ValueError as exc:
                    blocked.append(f"{item.action_id}:outcome:{exc}")
            resolved.append(
                ParallelResolvedAction(
                    action_id=item.action_id,
                    actor_id=item.actor_id,
                    priority=item.priority,
                    outcome=outcome,
                    preview=preview,
                    commands=commands,
                )
            )

        if blocked:
            return self._result(
                request, snapshot, "blocked", resolved, tuple(blocked)
            )
        if awaiting_checks:
            return self._result(
                request, snapshot, "awaiting_checks", resolved
            )

        commands, conflicts = self._merge_commands(resolved)
        if conflicts:
            return self._result(
                request, snapshot, "conflict", resolved, conflicts
            )
        try:
            resulting = self.kernel.preflight(snapshot, commands)
        except (TypeError, ValueError) as exc:
            return self._result(
                request,
                snapshot,
                "blocked",
                resolved,
                (f"atomic_preflight:{exc}",),
            )
        return self._result(
            request,
            snapshot,
            "ready",
            resolved,
            commands=commands,
            resulting_snapshot=resulting,
        )

    @staticmethod
    def _commands_for(
        preview: ResolutionPreview, outcome: str | None
    ) -> tuple[WorldCommand, ...]:
        if outcome is None:
            return ()
        return preview.commands_for_outcome(outcome)

    @classmethod
    def _merge_commands(
        cls, actions: list[ParallelResolvedAction]
    ) -> tuple[tuple[WorldCommand, ...], tuple[str, ...]]:
        commands: list[WorldCommand] = []
        exclusive: dict[tuple[str, str], tuple[Any, str]] = {}
        conflicts: list[str] = []
        fingerprints: set[str] = set()
        for action in actions:
            action_commands = action.commands
            if action.outcome is not None and action.preview.allowed:
                action_commands = resolved_action_commands(
                    action.preview,
                    action.outcome,
                    actor_id=action.actor_id,
                )
            for command in action_commands:
                assignments = exclusive_assignments(command)
                conflicting = False
                for target, value in assignments:
                    prior = exclusive.get(target)
                    if prior is not None and prior[0] != value:
                        conflicts.append(
                            f"{target[0]}:{target[1]}:{prior[1]}:{action.action_id}"
                        )
                        conflicting = True
                        continue
                    exclusive[target] = (value, action.action_id)
                if conflicting:
                    continue
                fingerprint = json.dumps(
                    command.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                # Equal assignments are idempotent; additive commands represent each
                # actor's separate cost and must never be collapsed.
                has_additive_effect = (
                    command.kind == "update_entity_runtime"
                    and "add_memory_ref" in command.payload
                )
                if not assignments or has_additive_effect or fingerprint not in fingerprints:
                    fingerprints.add(fingerprint)
                    commands.append(command)
        return tuple(commands), tuple(conflicts)

    @classmethod
    def _result(
        cls,
        request: ParallelSettlementRequest,
        snapshot: ScenarioSnapshot,
        status: ParallelSettlementStatus,
        actions: list[ParallelResolvedAction],
        conflicts: tuple[str, ...] = (),
        *,
        commands: tuple[WorldCommand, ...] = (),
        resulting_snapshot: ScenarioSnapshot | None = None,
    ) -> ParallelSettlementPreview:
        draft = ParallelSettlementPreview(
            batch_id=request.batch_id,
            run_id=snapshot.run_id,
            starting_run_version=snapshot.run_version,
            status=status,
            actions=tuple(actions),
            conflicts=conflicts,
            commands=commands,
            resulting_snapshot=resulting_snapshot or snapshot,
            request_hash=cls.request_hash(request),
        )
        return draft.model_copy(
            update={"settlement_hash": cls._hash(draft.canonical_payload())}
        )

    @staticmethod
    def _hash(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def request_hash(cls, request: ParallelSettlementRequest) -> str:
        """Bind idempotent commits to the complete, order-independent request."""

        ordered = sorted(
            request.intents, key=lambda item: (-item.priority, item.action_id)
        )
        return cls._hash(
            {
                "batch_id": request.batch_id,
                "intents": [item.model_dump(mode="json") for item in ordered],
            }
        )


__all__ = [
    "ParallelActionKernel",
    "ParallelIntent",
    "ParallelResolvedAction",
    "ParallelSettlementPreview",
    "ParallelSettlementRequest",
]
