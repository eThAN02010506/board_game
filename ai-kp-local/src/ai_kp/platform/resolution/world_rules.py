"""Deterministic semantic triggers and pressure-stage expansion."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ai_kp.platform.resolution.contracts import (
    ScenarioContract,
    ScenarioSnapshot,
    TriggerRule,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel

_MAX_EXPANSION_PASSES = 32


@dataclass(frozen=True)
class WorldRuleExpansion:
    commands: tuple[WorldCommand, ...]
    trigger_ids: tuple[str, ...]
    pressure_stage_ids: tuple[str, ...]


class DeterministicWorldRuleEngine:
    """Expand source-authored rules to a fixed point before one atomic commit.

    Models may emit a semantic event, but they never choose the consequences.
    Trigger and pressure commands come exclusively from the published contract.
    Endings are deliberately disabled during expansion and evaluated once, after
    every mandatory consequence has joined the final command batch.
    """

    def __init__(
        self,
        contract: ScenarioContract,
        *,
        kernel: ActionResolutionKernel | None = None,
    ):
        self.contract = contract
        if kernel is None:
            expansion_contract = contract.model_copy(update={"endings": ()})
            kernel = ActionResolutionKernel.from_contract(expansion_contract)
        self.kernel = kernel

    def expand(
        self,
        snapshot: ScenarioSnapshot,
        commands: Sequence[WorldCommand],
    ) -> WorldRuleExpansion:
        combined = list(commands)
        activated_pairs: set[tuple[str, int]] = set()
        activated_triggers: list[str] = []
        activated_stages: list[str] = []
        prior_triggers = self._recorded_ids(snapshot.events, "trigger_fired", "trigger_id")
        prior_triggers.update(
            self._command_marker_ids(commands, "trigger_fired", "trigger_id")
        )
        prior_stages = self._recorded_ids(
            snapshot.events, "pressure_stage_reached", "pressure_stage_id"
        )
        prior_stages.update(
            self._command_marker_ids(
                commands, "pressure_stage_reached", "pressure_stage_id"
            )
        )

        for _pass in range(_MAX_EXPANSION_PASSES):
            simulated = self.kernel.preflight(snapshot, tuple(combined))
            additions: list[WorldCommand] = []
            new_events = simulated.events[len(snapshot.events) :]
            for event_index, event in enumerate(new_events):
                event_type = str(event.get("type") or "")
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                for rule in self._matching_triggers(simulated, event_type, payload):
                    activation_key = (rule.trigger_id, event_index)
                    if activation_key in activated_pairs:
                        continue
                    if rule.once_per_run and (
                        rule.trigger_id in prior_triggers
                        or rule.trigger_id in activated_triggers
                    ):
                        continue
                    activated_pairs.add(activation_key)
                    marker_type = "trigger_fired" if rule.mandatory else "trigger_available"
                    if rule.mandatory:
                        additions.extend(rule.commands)
                        activated_triggers.append(rule.trigger_id)
                    additions.append(
                        WorldCommand(
                            kind="emit_event",
                            event_type=marker_type,
                            payload={
                                "trigger_id": rule.trigger_id,
                                "source_event_type": event_type,
                                "source_event_index": event_index,
                            },
                        )
                    )

            for pressure in self.contract.pressure_tracks:
                current = simulated.clocks.get(pressure.clock_id)
                if current is None:
                    continue
                for stage in pressure.stages:
                    stage_key = f"{pressure.pressure_id}:{stage.stage_id}"
                    if stage_key in prior_stages or stage_key in activated_stages:
                        continue
                    if current < stage.threshold:
                        continue
                    additions.extend(stage.commands)
                    additions.append(
                        WorldCommand(
                            kind="emit_event",
                            event_type="pressure_stage_reached",
                            payload={
                                "pressure_id": pressure.pressure_id,
                                "pressure_stage_id": stage_key,
                                "clock_id": pressure.clock_id,
                                "value": current,
                                "public_label": (
                                    stage.public_label
                                    if pressure.visibility == "table"
                                    else ""
                                ),
                                "public_description": (
                                    stage.public_description
                                    if pressure.visibility == "table"
                                    else ""
                                ),
                            },
                        )
                    )
                    activated_stages.append(stage_key)

            if not additions:
                return WorldRuleExpansion(
                    commands=tuple(combined),
                    trigger_ids=tuple(activated_triggers),
                    pressure_stage_ids=tuple(activated_stages),
                )
            combined.extend(additions)
        raise ValueError("World-rule expansion exceeded its bounded fixed-point limit")

    def _matching_triggers(
        self,
        snapshot: ScenarioSnapshot,
        event_type: str,
        payload: dict[str, Any],
    ) -> tuple[TriggerRule, ...]:
        event_target = payload.get("target_entity_id")
        return tuple(
            rule
            for rule in sorted(
                self.contract.trigger_rules,
                key=lambda item: (-item.priority, item.trigger_id),
            )
            if rule.event_type == event_type
            and (
                rule.target_entity_id is None
                or rule.target_entity_id == event_target
            )
            and self.kernel.conditions_satisfied(snapshot, rule.conditions)
        )

    @staticmethod
    def _recorded_ids(
        events: Sequence[dict[str, Any]], event_type: str, payload_key: str
    ) -> set[str]:
        return {
            str(payload[payload_key])
            for event in events
            if event.get("type") == event_type
            and isinstance((payload := event.get("payload")), dict)
            and payload.get(payload_key)
        }

    @staticmethod
    def _command_marker_ids(
        commands: Sequence[WorldCommand], event_type: str, payload_key: str
    ) -> set[str]:
        return {
            str(command.payload[payload_key])
            for command in commands
            if command.kind == "emit_event"
            and command.event_type == event_type
            and command.payload.get(payload_key)
        }


__all__ = ["DeterministicWorldRuleEngine", "WorldRuleExpansion"]
