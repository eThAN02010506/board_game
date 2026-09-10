"""Pure, bounded world settlement shared by runtime and static exploration."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from ai_kp.platform.resolution.contracts import (
    ScenarioContract,
    ScenarioSnapshot,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.reactive import (
    ReactiveEvent,
    ReactivePolicyEngine,
    ReactiveTrigger,
)
from ai_kp.platform.resolution.world_rules import DeterministicWorldRuleEngine

SettlementCause = Literal["action", "world_expansion"]

_MAX_REACTIVE_EVENTS = 64
_MAX_REACTIVE_ACTIVATIONS = 256


@dataclass(frozen=True)
class _SettlementContext:
    """Immutable reducers compiled once for one canonical contract value."""

    final_kernel: ActionResolutionKernel
    intermediate_kernel: ActionResolutionKernel
    world_rules: DeterministicWorldRuleEngine
    reactive: ReactivePolicyEngine | None


def _contract_cache_key(contract: ScenarioContract) -> str:
    return json.dumps(
        contract.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@lru_cache(maxsize=64)
def _settlement_context(contract_json: str) -> _SettlementContext:
    contract = ScenarioContract.model_validate_json(contract_json)
    final_kernel = ActionResolutionKernel.from_contract(contract)
    intermediate_contract = contract.model_copy(update={"endings": ()})
    intermediate_kernel = ActionResolutionKernel.from_contract(intermediate_contract)
    return _SettlementContext(
        final_kernel=final_kernel,
        intermediate_kernel=intermediate_kernel,
        world_rules=DeterministicWorldRuleEngine(
            contract, kernel=intermediate_kernel
        ),
        reactive=(
            ReactivePolicyEngine(contract, kernel=intermediate_kernel)
            if contract.reactive_policies
            else None
        ),
    )


def clear_world_settlement_cache() -> None:
    """Clear the bounded immutable context cache for tests and operations."""

    _settlement_context.cache_clear()


class WorldSettlementEngine:
    """Reusable immutable settlement context for one canonical contract."""

    def __init__(self, contract: ScenarioContract):
        self._context = _settlement_context(_contract_cache_key(contract))

    @property
    def kernel(self) -> ActionResolutionKernel:
        return self._context.final_kernel

    def settle(
        self,
        starting: ScenarioSnapshot,
        commands: tuple[WorldCommand, ...],
        *,
        cause: SettlementCause = "action",
    ) -> tuple[tuple[WorldCommand, ...], ScenarioSnapshot]:
        return _settle_world_commands(
            self._context,
            starting,
            commands,
            cause=cause,
        )


def settle_world_commands(
    contract: ScenarioContract,
    starting: ScenarioSnapshot,
    commands: tuple[WorldCommand, ...],
    *,
    cause: SettlementCause = "action",
) -> tuple[tuple[WorldCommand, ...], ScenarioSnapshot]:
    """Settle through the cached immutable context for a contract value."""

    return WorldSettlementEngine(contract).settle(starting, commands, cause=cause)


def _settle_world_commands(
    context: _SettlementContext,
    starting: ScenarioSnapshot,
    commands: tuple[WorldCommand, ...],
    *,
    cause: SettlementCause,
) -> tuple[tuple[WorldCommand, ...], ScenarioSnapshot]:
    """Settle one atomic command batch to a bounded causal fixed point.

    Intermediate reducers deliberately exclude endings. A terminal rule is
    evaluated exactly once, after every deterministic semantic consequence and
    lifecycle reaction has stabilized. Any cycle which exceeds the bounds
    raises before a repository can persist a partial snapshot.
    """

    # Re-expanding a previous TriggerRule expansion could replay a non-once
    # semantic rule. Rebuild from caller- and reaction-authored commands only.
    source_commands = list(commands)
    expanded = context.world_rules.expand(starting, source_commands).commands
    if context.reactive is None:
        # Semantic TriggerRule and pressure expansion already ran above. With no
        # lifecycle policies there is no event consumer, so avoid constructing
        # transient snapshots solely to evaluate empty trigger queues.
        return expanded, context.final_kernel.preflight(starting, expanded)
    simulated = context.intermediate_kernel.preflight(starting, expanded)

    pending: deque[ReactiveEvent] = deque()
    next_event_ordinal = 0

    def enqueue(trigger: ReactiveTrigger) -> None:
        nonlocal next_event_ordinal
        pending.append(
            ReactiveEvent(
                event_id=_event_id(starting, next_event_ordinal, trigger),
                trigger=trigger,
            )
        )
        next_event_ordinal += 1

    if cause == "action":
        enqueue("after_action")
    # Contract activation is administrative. Registration itself is not a clock
    # advance or scene entry, while commands produced by its semantic TriggerRule
    # still are real lifecycle transitions and must join the closure.
    transition_clock_ids = None
    include_scene_transitions = True
    if cause == "world_expansion":
        transition_clock_ids = {
            str(command.clock_id)
            for command in expanded
            if command.kind == "advance_clock" and command.clock_id
        }
        include_scene_transitions = any(
            command.kind in {"set_scene", "move_actor"} for command in expanded
        )
    for trigger in _lifecycle_transitions(
        starting,
        simulated,
        clock_ids=transition_clock_ids,
        include_scene=include_scene_transitions,
    ):
        enqueue(trigger)

    processed_events = 0
    activation_count = 0
    while pending:
        if processed_events >= _MAX_REACTIVE_EVENTS:
            raise ValueError("Reactive settlement exceeded its bounded event limit")
        event = pending.popleft()
        processed_events += 1
        decision = context.reactive.evaluate(simulated, event)
        if not decision.activations:
            continue
        activation_count += len(decision.activations)
        if activation_count > _MAX_REACTIVE_ACTIVATIONS:
            raise ValueError("Reactive settlement exceeded its bounded activation limit")

        previous = simulated
        source_commands.extend(
            command
            for activation in decision.activations
            for command in activation.commands
        )
        expanded = context.world_rules.expand(starting, source_commands).commands
        simulated = context.intermediate_kernel.preflight(starting, expanded)
        for trigger in _lifecycle_transitions(previous, simulated):
            enqueue(trigger)

    # Endings and their commands run once on the fully expanded authoritative
    # batch. This restores the public one-version-per-settlement invariant.
    return expanded, context.final_kernel.preflight(starting, expanded)


def _lifecycle_transitions(
    before: ScenarioSnapshot,
    after: ScenarioSnapshot,
    *,
    clock_ids: set[str] | None = None,
    include_scene: bool = True,
) -> Iterable[ReactiveTrigger]:
    """Yield each schema-level lifecycle category at most once per state wave."""

    eligible_clock_ids = set(before.clocks) | set(after.clocks)
    if clock_ids is not None:
        eligible_clock_ids &= clock_ids
    if any(
        before.clocks.get(clock_id) != after.clocks.get(clock_id)
        for clock_id in eligible_clock_ids
    ):
        yield "clock_advanced"

    if not include_scene:
        return
    actor_ids = set(before.actor_locations) | set(after.actor_locations)
    if before.scene_id != after.scene_id or any(
        before.actor_locations.get(actor_id) != after.actor_locations.get(actor_id)
        for actor_id in actor_ids
    ):
        yield "scene_entered"


def _event_id(snapshot: ScenarioSnapshot, ordinal: int, trigger: str) -> str:
    authority = hashlib.sha256(
        f"{snapshot.run_id}:{snapshot.run_version}".encode()
    ).hexdigest()[:20]
    return f"settlement:{authority}:{ordinal}:{trigger}"


__all__ = [
    "SettlementCause",
    "WorldSettlementEngine",
    "clear_world_settlement_cache",
    "settle_world_commands",
]
