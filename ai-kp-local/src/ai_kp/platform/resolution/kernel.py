"""Pure deterministic action preview and world-command reducer."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ai_kp.platform.resolution.contracts import (
    ActionIntent,
    ActionOperator,
    CheckPlan,
    EndingRule,
    EntityRuntimeState,
    ResolutionPreview,
    ScenarioContract,
    ScenarioSnapshot,
    StateCondition,
    WorldCommand,
)


class ScenarioStateQuery:
    """Read-only query view that materializes one snapshot exactly once."""

    def __init__(
        self, kernel: ActionResolutionKernel, snapshot: ScenarioSnapshot
    ) -> None:
        kernel._validate_snapshot_binding(snapshot)
        self._kernel = kernel
        self._state = snapshot.model_dump(mode="python")

    def condition_satisfied(self, condition: StateCondition) -> bool:
        return self._kernel._evaluate_condition_state(self._state, condition)

    def conditions_satisfied(self, conditions: Sequence[StateCondition]) -> bool:
        return all(self.condition_satisfied(item) for item in conditions)

    def state_value(self, path: str) -> tuple[bool, Any]:
        return self._kernel._read_path(self._state, path)


def operator_outcome_path(operator_id: str) -> str:
    """Return a path-safe stable key for one kernel-produced operator outcome."""

    digest = hashlib.sha256(operator_id.encode("utf-8")).hexdigest()
    return f"events.operator_outcomes.{digest}.outcome"


class ActionResolutionKernel:
    """Deterministically previews operators and preflights command batches."""

    def __init__(
        self,
        operators: Iterable[ActionOperator],
        *,
        contract: ScenarioContract | None = None,
    ):
        indexed: dict[str, ActionOperator] = {}
        for operator in operators:
            if operator.operator_id in indexed:
                raise ValueError(f"Duplicate operator id: {operator.operator_id}")
            indexed[operator.operator_id] = operator
        self._operators = indexed
        self._contract = contract
        if contract is None or not contract.endings:
            self._unsafe_ending_operator_ids: frozenset[str] = frozenset()
        else:
            from ai_kp.platform.resolution.causal_validation import (
                unsafe_ending_operator_ids,
            )

            self._unsafe_ending_operator_ids = unsafe_ending_operator_ids(contract)

    @classmethod
    def from_contract(cls, contract: ScenarioContract) -> ActionResolutionKernel:
        return cls(contract.operators, contract=contract)

    def preview(self, snapshot: ScenarioSnapshot, intent: ActionIntent) -> ResolutionPreview:
        query = self.query_snapshot(snapshot)
        if snapshot.status == "completed":
            raise ValueError("Completed scenario runs reject new actions")
        if snapshot.status != "active":
            raise ValueError("Only an active scenario run accepts actions")
        if not intent.operator_id:
            raise ValueError("An authoritative preview requires an operator_id")
        operator = self._operators.get(intent.operator_id)
        if operator is None:
            raise KeyError(f"Unknown action operator: {intent.operator_id}")

        unsafe_terminal = operator.operator_id in self._unsafe_ending_operator_ids
        failed = [
            condition for condition in operator.preconditions
            if not query.condition_satisfied(condition)
        ]
        allowed = (
            not unsafe_terminal
            and not failed
            and operator.policy not in {"impossible", "clarification"}
        )
        if unsafe_terminal:
            reason = (
                "该动作不能证明结局已经发生；请描述会实际改变世界状态的做法，"
                "或先完成契约要求的前置步骤。"
            )
        elif failed:
            reason = "Preconditions not met: " + ", ".join(item.path for item in failed)
        elif operator.policy == "impossible":
            reason = operator.clarification_prompt or "The action is impossible in this state"
        elif operator.policy == "clarification":
            reason = operator.clarification_prompt or "The action requires clarification"
        else:
            reason = operator.rationale or "Operator preconditions satisfied"

        selected = self._select_skill(operator, intent)
        check_plan = (
            CheckPlan(
                choices=operator.skill_choices,
                selected_skill_key=selected,
                automatic_information=tuple(
                    dict.fromkeys(
                        (
                            *operator.automatic_information,
                            *(
                                item
                                for choice in operator.skill_choices
                                for item in choice.automatic_information
                            ),
                        )
                    )
                ),
            )
            if allowed and selected is not None
            else None
        )
        draft = ResolutionPreview(
            action_id=intent.action_id,
            run_id=snapshot.run_id,
            contract_id=snapshot.contract_id,
            scenario_version=snapshot.scenario_version,
            run_version=snapshot.run_version,
            operator_id=operator.operator_id,
            policy=operator.policy,
            allowed=allowed,
            reason=reason,
            skill_choices=operator.skill_choices if allowed else (),
            selected_skill_key=selected if allowed else None,
            check_plan=check_plan,
            automatic_information=(
                operator.automatic_information if allowed else ()
            ),
            response_obligation_ids=(
                operator.response_obligation_ids if allowed else ()
            ),
            always_commands=(
                self._bind_actor(operator.always_commands, intent.actor_id)
                if allowed
                else ()
            ),
            success_commands=(
                self._bind_actor(operator.success_commands, intent.actor_id)
                if allowed
                else ()
            ),
            failure_commands=(
                self._bind_actor(operator.failure_commands, intent.actor_id)
                if allowed
                else ()
            ),
            outcome_branches=(
                tuple(
                    branch.model_copy(
                        update={
                            "commands": self._bind_actor(
                                branch.commands, intent.actor_id
                            )
                        }
                    )
                    for branch in operator.outcome_branches
                )
                if allowed
                else ()
            ),
            maximum_effect=operator.maximum_effect,
        )
        return draft.model_copy(update={"preview_hash": self._hash(draft.canonical_payload())})

    @staticmethod
    def _bind_actor(
        commands: Sequence[WorldCommand], actor_id: str
    ) -> tuple[WorldCommand, ...]:
        return tuple(
            command.model_copy(update={"actor_id": actor_id})
            if command.kind == "apply_ruleset_effect"
            else command
            for command in commands
        )

    def preflight(
        self,
        snapshot: ScenarioSnapshot,
        commands: Sequence[WorldCommand],
    ) -> ScenarioSnapshot:
        """Apply one atomic batch to a copy and evaluate generic ending rules."""

        self._validate_snapshot_binding(snapshot, commands)
        if snapshot.status == "completed" and commands:
            raise ValueError("Completed scenario runs reject world commands")
        state = snapshot.model_dump(mode="python")
        state["events"] = list(state["events"])
        for index, command in enumerate(commands):
            if state["status"] == "completed":
                raise ValueError(f"Command {index} occurs after scenario completion")
            self._apply_command(state, command)
        if commands and state["status"] != "completed":
            self._apply_first_matching_ending(state)
        self._validate_bounds(state)
        state["events"] = tuple(state["events"])
        state["run_version"] = snapshot.run_version + (1 if commands else 0)
        return ScenarioSnapshot.model_validate(state)

    def conditions_satisfied(
        self,
        snapshot: ScenarioSnapshot,
        conditions: Sequence[StateCondition],
    ) -> bool:
        """Expose the same closed evaluator to planners and reactive policies."""

        return self.query_snapshot(snapshot).conditions_satisfied(conditions)

    def state_value(self, snapshot: ScenarioSnapshot, path: str) -> tuple[bool, Any]:
        """Read one contract state path without exposing mutable snapshot internals."""

        return self.query_snapshot(snapshot).state_value(path)

    def query_snapshot(self, snapshot: ScenarioSnapshot) -> ScenarioStateQuery:
        """Build one reusable condition/value reader for a committed snapshot."""

        return ScenarioStateQuery(self, snapshot)

    def _validate_snapshot_binding(
        self,
        snapshot: ScenarioSnapshot,
        commands: Sequence[WorldCommand] = (),
    ) -> None:
        if self._contract is None:
            return
        if snapshot.contract_id != self._contract.contract_id:
            raise ValueError("Snapshot belongs to a different scenario contract")
        if snapshot.scenario_version == self._contract.source_version:
            return
        activations = [
            (index, command)
            for index, command in enumerate(commands)
            if command.kind == "activate_contract_overlay"
        ]
        if (
            snapshot.scenario_version + 1 == self._contract.source_version
            and len(activations) == 1
            and activations[0][0] == 0
            and activations[0][1].payload.get("source_version")
            == self._contract.source_version
        ):
            return
        raise ValueError("Snapshot scenario version does not match the contract")

    @staticmethod
    def _select_skill(operator: ActionOperator, intent: ActionIntent) -> str | None:
        if not operator.skill_choices:
            if intent.requested_skill_key:
                raise ValueError("This operator does not accept a skill check")
            return None
        keys = {choice.skill_key for choice in operator.skill_choices}
        if intent.requested_skill_key is None:
            return operator.skill_choices[0].skill_key
        if intent.requested_skill_key not in keys:
            raise ValueError("Requested skill is not authorized by this operator")
        return intent.requested_skill_key

    def _apply_first_matching_ending(self, state: dict[str, Any]) -> None:
        if self._contract is None:
            return
        ordered = sorted(self._contract.endings, key=lambda item: (-item.priority, item.ending_id))
        for ending in ordered:
            if not self._ending_matches(state, ending):
                continue
            for command in ending.commands:
                self._apply_command(state, command)
            self._apply_command(state, WorldCommand(kind="complete_run", value=ending.ending_id))
            return

    def _ending_matches(self, state: dict[str, Any], ending: EndingRule) -> bool:
        snapshot = ScenarioSnapshot.model_validate({**state, "events": tuple(state["events"])})
        query = self.query_snapshot(snapshot)
        return query.conditions_satisfied(ending.all_conditions) and (
            not ending.any_conditions
            or any(query.condition_satisfied(item) for item in ending.any_conditions)
        )

    def _validate_bounds(self, state: dict[str, Any]) -> None:
        if self._contract is None:
            if any(value < 0 for value in state["resources"].values()):
                raise ValueError("Resource cannot become negative")
            if any(value < 0 for value in state["clocks"].values()):
                raise ValueError("Clock cannot become negative")
            return
        for clock in self._contract.clocks:
            value = state["clocks"].get(clock.clock_id)
            if value is None or value < 0 or value > clock.maximum_value:
                raise ValueError(f"Clock is outside contract bounds: {clock.clock_id}")
        for resource in self._contract.resources:
            value = state["resources"].get(resource.resource_id)
            if value is None or value < resource.minimum_value:
                raise ValueError(f"Resource is outside contract bounds: {resource.resource_id}")
            if resource.maximum_value is not None and value > resource.maximum_value:
                raise ValueError(f"Resource is outside contract bounds: {resource.resource_id}")

    @classmethod
    def _evaluate_condition_state(
        cls, state: Mapping[str, Any], condition: StateCondition
    ) -> bool:
        found, actual = cls._read_path(state, condition.path)
        if condition.operator == "exists":
            return found
        if condition.operator == "not_exists":
            return not found
        if not found:
            return False
        expected = condition.value
        if condition.operator == "eq":
            return actual == expected
        if condition.operator == "ne":
            return actual != expected
        if condition.operator == "contains":
            return isinstance(actual, (str, list, tuple, set, dict)) and expected in actual
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            return False
        return {
            "gt": actual > expected,
            "gte": actual >= expected,
            "lt": actual < expected,
            "lte": actual <= expected,
        }[condition.operator]

    @classmethod
    def _apply_command(cls, state: dict[str, Any], command: WorldCommand) -> None:
        if command.kind == "set_fact":
            cls._write_path(state["facts"], command.path or "", copy.deepcopy(command.value))
        elif command.kind == "remove_fact":
            cls._remove_path(state["facts"], command.path or "")
        elif command.kind == "set_entity_status":
            state["entities"][command.entity_id] = str(command.value)
            runtime = state["entity_runtime"].get(command.entity_id)
            if runtime is not None:
                if isinstance(runtime, EntityRuntimeState):
                    runtime = runtime.model_dump(mode="python")
                state["entity_runtime"][command.entity_id] = {
                    **runtime,
                    "status": str(command.value),
                }
        elif command.kind == "update_entity_runtime":
            runtime = state["entity_runtime"].get(command.entity_id)
            if runtime is None:
                runtime = EntityRuntimeState(
                    status=state["entities"].get(command.entity_id, "active")
                ).model_dump(mode="python")
            elif isinstance(runtime, EntityRuntimeState):
                runtime = runtime.model_dump(mode="python")
            else:
                runtime = dict(runtime)
            updates = dict(command.payload)
            memory_ref = updates.pop("add_memory_ref", None)
            runtime.update(updates)
            if memory_ref is not None:
                runtime["memory_refs"] = tuple(
                    dict.fromkeys((*runtime.get("memory_refs", ()), memory_ref))
                )[-32:]
            state["entity_runtime"][command.entity_id] = runtime
        elif command.kind == "move_actor":
            state["actor_locations"][command.actor_id] = str(command.value)
            runtime = state["entity_runtime"].get(command.actor_id)
            if runtime is not None:
                if isinstance(runtime, EntityRuntimeState):
                    runtime = runtime.model_dump(mode="python")
                state["entity_runtime"][command.actor_id] = {
                    **runtime,
                    "location_id": str(command.value),
                }
        elif command.kind == "adjust_resource":
            path = command.path or ""
            current = state["resources"].get(path, 0)
            updated = current + (command.delta or 0)
            state["resources"][path] = updated
        elif command.kind == "advance_clock":
            clock_id = command.clock_id or ""
            updated = state["clocks"].get(clock_id, 0) + (command.delta or 0)
            state["clocks"][clock_id] = updated
        elif command.kind == "set_scene":
            state["scene_id"] = str(command.value)
        elif command.kind == "complete_run":
            state["status"] = "completed"
            state["ending_id"] = str(command.value)
        elif command.kind == "emit_event":
            state["events"].append(
                {"type": command.event_type, "payload": copy.deepcopy(command.payload)}
            )
        elif command.kind == "set_world_entity_state":
            # The shared campaign entity ledger owns values; never mirror them
            # as mutable scenario facts with a second competing authority.
            state["events"].append({
                "type": "world_entity_state_requested",
                "payload": {
                    "entity_id": command.entity_id,
                    "dimension": command.path,
                    "value": copy.deepcopy(command.value),
                    "visibility": command.payload["visibility"],
                },
            })
        elif command.kind == "apply_ruleset_effect":
            # The pure scenario reducer records intent only. The application layer
            # consumes the same persisted command transactionally through the
            # installed ruleset; character state never gets duplicated here.
            state["events"].append(
                {
                    "type": "ruleset_effect_requested",
                    "payload": {
                        "effect_key": command.event_type,
                        "parameters": copy.deepcopy(command.payload),
                    },
                }
            )
        elif command.kind == "activate_contract_overlay":
            state["scenario_version"] = int(command.payload["source_version"])
            state["events"].append(
                {"type": "contract_overlay_activated", "payload": {"contract_hash": command.value}}
            )
        elif command.kind == "register_entity":
            if command.entity_id in state["entities"]:
                raise ValueError(f"Entity already exists: {command.entity_id}")
            state["entities"][command.entity_id] = str(command.value)
            state["entity_runtime"][command.entity_id] = EntityRuntimeState(
                status=str(command.value)
            ).model_dump(mode="python")
        elif command.kind == "register_clock":
            if command.clock_id in state["clocks"]:
                raise ValueError(f"Clock already exists: {command.clock_id}")
            state["clocks"][command.clock_id] = command.value
        elif command.kind == "register_resource":
            path = command.path or ""
            if path in state["resources"]:
                raise ValueError(f"Resource already exists: {path}")
            state["resources"][path] = command.value

    @staticmethod
    def _read_path(root: Mapping[str, Any], path: str) -> tuple[bool, Any]:
        parts = path.split(".")
        if len(parts) >= 4 and parts[:2] == ["events", "operator_outcomes"]:
            events = root.get("events")
            if not isinstance(events, (list, tuple)):
                return False, None
            event_key = parts[2]
            matched = next(
                (
                    event
                    for event in reversed(events)
                    if isinstance(event, Mapping)
                    and event.get("type") == "action_resolved"
                    and isinstance(event.get("payload"), Mapping)
                    and hashlib.sha256(
                        str(event["payload"].get("operator_id", "")).encode("utf-8")
                    ).hexdigest()
                    == event_key
                ),
                None,
            )
            if matched is None:
                return False, None
            current: Any = matched["payload"]
            parts = parts[3:]
        else:
            current = root
        for part in parts:
            if not isinstance(current, Mapping) or part not in current:
                return False, None
            current = current[part]
        return True, current

    @staticmethod
    def _write_path(root: dict[str, Any], path: str, value: Any) -> None:
        parts = [part for part in path.split(".") if part]
        if not parts:
            raise ValueError("Command path cannot be empty")
        current = root
        for part in parts[:-1]:
            child = current.setdefault(part, {})
            if not isinstance(child, dict):
                raise TypeError(f"Cannot write through non-object path: {path}")
            current = child
        current[parts[-1]] = value

    @staticmethod
    def _remove_path(root: dict[str, Any], path: str) -> None:
        parts = [part for part in path.split(".") if part]
        if not parts:
            raise ValueError("Command path cannot be empty")
        current = root
        for part in parts[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                return
            current = child
        current.pop(parts[-1], None)

    @staticmethod
    def _hash(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = ["ActionResolutionKernel", "ScenarioStateQuery", "operator_outcome_path"]
