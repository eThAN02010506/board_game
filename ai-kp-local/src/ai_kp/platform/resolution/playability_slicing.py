"""Pure causal slicing for bounded scenario playability proofs.

The slicer has no exploration state or caches.  It only classifies the closed
contract DSL and returns an immutable dependency component.  ``None`` means the
contract uses a read/write shape whose projection has not been proved sound;
callers must retain global exact-state exploration in that case.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    ScenarioContract,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import operator_outcome_path


@dataclass(frozen=True)
class ProofSlice:
    """Closed state and operator component for one proof."""

    operator_ids: frozenset[str]
    state_paths: frozenset[str]


def causal_proof_slice(
    contract: ScenarioContract,
    seed_paths: set[str],
    *,
    target_operator_ids: set[str],
) -> ProofSlice | None:
    """Close proof targets over producers and authoritative settlement effects.

    The closure is deliberately conservative: all ending conditions remain
    because an unrelated terminal can block a later witness, and all semantic,
    pressure, and lifecycle consequences caused by selected actions remain
    because one invalid consequence rejects the entire atomic runtime batch.
    """

    paths = set(seed_paths)
    paths.update(
        condition.path
        for ending in contract.endings
        for condition in (*ending.all_conditions, *ending.any_conditions)
    )
    selected = set(target_operator_ids)
    available_events: set[str] = {"action_resolved"} if selected else set()
    required_events: set[str] = set()
    lifecycle: set[str] = {"after_action"} if selected else set()
    required_lifecycle: set[str] = set()
    active_triggers: set[str] = set()
    active_pressures: set[tuple[str, str]] = set()
    active_reactive: set[tuple[str, str]] = set()

    known_outcome_paths = {
        operator_outcome_path(operator.operator_id): operator.operator_id
        for operator in contract.operators
    }

    def conditions_supported() -> bool:
        return all(
            not path.startswith("events.") or path in known_outcome_paths
            for path in paths
        )

    def add_conditions(conditions: Sequence[StateCondition]) -> None:
        paths.update(condition.path for condition in conditions)

    def add_commands(commands: Sequence[WorldCommand]) -> bool:
        for command in commands:
            writes = command_state_paths(command)
            if writes is None:
                return False
            paths.update(writes)
            if command.kind == "emit_event" and command.event_type:
                available_events.add(command.event_type)
            if command.kind in {"set_scene", "move_actor"}:
                lifecycle.add("scene_entered")
            if command.kind == "advance_clock":
                lifecycle.add("clock_advanced")
        return True

    # Ending commands execute in the terminal kernel batch. Their bounded state
    # operands determine whether that whole batch is valid.
    for ending in contract.endings:
        if not add_commands(ending.commands):
            return None

    while True:
        before = (
            frozenset(paths),
            frozenset(selected),
            frozenset(available_events),
            frozenset(required_events),
            frozenset(lifecycle),
            frozenset(required_lifecycle),
            frozenset(active_triggers),
            frozenset(active_pressures),
            frozenset(active_reactive),
        )
        if not conditions_supported():
            return None

        selected.update(
            operator_id
            for path, operator_id in known_outcome_paths.items()
            if path in paths
        )
        if (
            "after_action" in required_lifecycle
            or "action_resolved" in required_events
        ):
            # Every accepted operator settlement creates both signals. There is
            # no narrower producer class in the authoritative DSL.
            selected.update(operator.operator_id for operator in contract.operators)
        for operator in contract.operators:
            commands = _operator_commands(operator)
            command_paths = commands_state_paths(commands)
            if command_paths is None:
                return None
            command_events = {
                command.event_type
                for command in commands
                if command.kind == "emit_event" and command.event_type
            }
            command_lifecycle = {
                trigger
                for command in commands
                for trigger in _command_lifecycle(command)
            }
            if (
                paths_overlap(command_paths, paths)
                or bool(command_events & required_events)
                or bool(command_lifecycle & required_lifecycle)
            ):
                selected.add(operator.operator_id)

        # Forward-close every effect in a selected outcome. This preserves
        # bounded-command validity as well as future eligibility.
        for operator in contract.operators:
            if operator.operator_id not in selected:
                continue
            add_conditions(operator.preconditions)
            available_events.add("action_resolved")
            lifecycle.add("after_action")
            if not add_commands(_operator_commands(operator)):
                return None

        for rule in contract.trigger_rules:
            command_paths = commands_state_paths(rule.commands)
            if command_paths is None:
                return None
            output_relevant = paths_overlap(command_paths, paths) or any(
                command.kind == "emit_event"
                and command.event_type in required_events
                for command in rule.commands
            )
            if output_relevant:
                required_events.add(rule.event_type)
            if (
                rule.event_type in available_events
                or rule.event_type in required_events
            ):
                active_triggers.add(rule.trigger_id)
        for rule in contract.trigger_rules:
            if rule.trigger_id not in active_triggers:
                continue
            add_conditions(rule.conditions)
            if not add_commands(rule.commands):
                return None

        for pressure in contract.pressure_tracks:
            clock_path = f"clocks.{pressure.clock_id}"
            for stage in pressure.stages:
                command_paths = commands_state_paths(stage.commands)
                if command_paths is None:
                    return None
                if (
                    clock_path in paths
                    or paths_overlap(command_paths, paths)
                    or "pressure_stage_reached" in required_events
                    or any(
                        command.kind == "emit_event"
                        and command.event_type in required_events
                        for command in stage.commands
                    )
                ):
                    active_pressures.add((pressure.pressure_id, stage.stage_id))
            for stage in pressure.stages:
                if (pressure.pressure_id, stage.stage_id) not in active_pressures:
                    continue
                paths.add(clock_path)
                if not add_commands(stage.commands):
                    return None
                available_events.add("pressure_stage_reached")

        # ReactivePolicy is a priority selector. Any sibling with the same
        # lifecycle trigger can shadow a relevant rule, so retain all siblings.
        for policy in contract.reactive_policies:
            relevant_triggers: set[str] = set()
            for rule in policy.rules:
                command_paths = commands_state_paths(rule.commands)
                if command_paths is None:
                    return None
                if paths_overlap(command_paths, paths) or any(
                    command.kind == "emit_event"
                    and command.event_type in required_events
                    for command in rule.commands
                ):
                    required_lifecycle.add(rule.trigger)
                if (
                    rule.trigger in lifecycle
                    or rule.trigger in required_lifecycle
                ):
                    relevant_triggers.add(rule.trigger)
            for rule in policy.rules:
                if rule.trigger in relevant_triggers:
                    active_reactive.add((policy.policy_id, rule.rule_id))
        for policy in contract.reactive_policies:
            for rule in policy.rules:
                if (policy.policy_id, rule.rule_id) not in active_reactive:
                    continue
                lifecycle.add(rule.trigger)
                add_conditions(rule.conditions)
                if not add_commands(rule.commands):
                    return None

        after = (
            frozenset(paths),
            frozenset(selected),
            frozenset(available_events),
            frozenset(required_events),
            frozenset(lifecycle),
            frozenset(required_lifecycle),
            frozenset(active_triggers),
            frozenset(active_pressures),
            frozenset(active_reactive),
        )
        if after == before:
            return ProofSlice(
                operator_ids=frozenset(selected),
                state_paths=frozenset(paths),
            )


def _operator_commands(operator: ActionOperator) -> tuple[WorldCommand, ...]:
    return (
        *operator.always_commands,
        *operator.success_commands,
        *operator.failure_commands,
        *(
            command
            for branch in operator.outcome_branches
            for command in branch.commands
        ),
    )


def _command_lifecycle(command: WorldCommand) -> tuple[str, ...]:
    if command.kind in {"set_scene", "move_actor"}:
        return ("scene_entered",)
    if command.kind == "advance_clock":
        return ("clock_advanced",)
    return ()


def paths_overlap(left: set[str], right: set[str]) -> bool:
    return any(
        first == second
        or first.startswith(f"{second}.")
        or second.startswith(f"{first}.")
        for first in left
        for second in right
    )


def commands_state_paths(commands: Sequence[WorldCommand]) -> set[str] | None:
    paths: set[str] = set()
    for command in commands:
        writes = command_state_paths(command)
        if writes is None:
            return None
        paths.update(writes)
    return paths


def command_state_paths(command: WorldCommand) -> set[str] | None:
    """Return exact state writes, or ``None`` for an unproved command shape."""

    if command.kind in {"set_fact", "remove_fact"}:
        return {f"facts.{command.path}"}
    if command.kind == "set_entity_status":
        return {
            f"entities.{command.entity_id}",
            f"entity_runtime.{command.entity_id}.status",
        }
    if command.kind == "update_entity_runtime":
        keys = set(command.payload)
        if "add_memory_ref" in keys:
            keys.remove("add_memory_ref")
            keys.add("memory_refs")
        return {f"entity_runtime.{command.entity_id}.{key}" for key in keys}
    if command.kind == "move_actor":
        return {
            f"actor_locations.{command.actor_id}",
            f"entity_runtime.{command.actor_id}.location_id",
        }
    if command.kind == "adjust_resource":
        return {f"resources.{command.path}"}
    if command.kind == "advance_clock":
        return {f"clocks.{command.clock_id}"}
    if command.kind == "set_scene":
        return {"scene_id"}
    if command.kind in {"emit_event", "apply_ruleset_effect", "set_world_entity_state"}:
        return set()
    # Control/registration mutations are not legal in compiled scenario
    # operators today. A future DSL expansion must add an explicit mapping.
    return None


__all__ = ["ProofSlice", "causal_proof_slice"]
