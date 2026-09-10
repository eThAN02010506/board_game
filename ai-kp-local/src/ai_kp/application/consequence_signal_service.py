"""Read-only application service for current and recent world consequence signals."""

from __future__ import annotations

import hashlib
from typing import Any

from ai_kp.platform.resolution.consequence_signals import (
    ConsequenceSignalProjection,
    ConsequenceSignalProjector,
    SignalAudience,
)


class ConsequenceSignalService:
    def __init__(self, repo: Any):
        self.repo = repo

    def campaign_view(
        self, campaign_id: str, *, audience: SignalAudience
    ) -> dict[str, Any]:
        run = self.repo.get_active_campaign_module_run(campaign_id)
        if run is None:
            recent = self.repo.list_campaign_module_runs(campaign_id, limit=1)
            if not recent:
                return {
                    "run_id": None,
                    "state_version": None,
                    "signals": [],
                    "events": [],
                }
            # Preserve the final public consequence state for the ending screen.
            # This is a read-only projection; action submission remains locked by
            # the module-run lifecycle.
            run = recent[0]
        run_id = str(run["id"])
        try:
            binding = self.repo.get_module_run_contract_binding(run_id)
            # A newly bound run has no snapshot until the first kernel operation.
            # Signals must still project the contract's initial world state, so use
            # the repository's idempotent initializer at this read boundary.
            state = self.repo.initialize_scenario_run_state(run_id)
        except KeyError:
            return {"run_id": run_id, "state_version": None, "signals": [], "events": []}

        contract = binding["contract"]
        projector = ConsequenceSignalProjector(contract)
        current = projector.project(state["snapshot"], audience=audience)
        events = self._recent_events(
            run_id,
            projector,
            audience=audience,
            scenario_version=contract.source_version,
        )
        return {
            "run_id": run_id,
            "state_version": (
                state["state_version"] if audience == "kp" else None
            ),
            "signals": [item.model_dump(mode="json") for item in current],
            "events": events,
        }

    def _recent_events(
        self,
        run_id: str,
        projector: ConsequenceSignalProjector,
        *,
        audience: SignalAudience,
        scenario_version: int,
    ) -> list[dict[str, Any]]:
        previous: dict[str, ConsequenceSignalProjection] = {}
        events: list[dict[str, Any]] = []
        batches = self.repo.list_recent_scenario_command_batches(run_id, limit=21)
        eligible = [
            batch
            for batch in batches
            if batch["snapshot"].scenario_version == scenario_version
        ]
        if eligible and int(eligible[0]["result_version"]) == 1:
            previous = {
                item.signal_id: item
                for item in projector.project(
                    projector.contract.initial_snapshot(run_id), audience=audience
                )
            }
        elif eligible:
            previous = {
                item.signal_id: item
                for item in projector.project(
                    eligible[0]["snapshot"], audience=audience
                )
            }
            eligible = eligible[1:]
        for batch in eligible:
            snapshot = batch["snapshot"]
            current = {
                item.signal_id: item
                for item in projector.project(snapshot, audience=audience)
            }
            for signal_id in sorted(set(previous) | set(current)):
                before = previous.get(signal_id)
                after = current.get(signal_id)
                if self._signature(before) == self._signature(after):
                    continue
                events.append(
                    {
                        "batch_id": (
                            str(batch["id"])
                            if audience == "kp"
                            else self._public_event_id(str(batch["id"]), signal_id)
                        ),
                        "result_version": (
                            batch["result_version"]
                            if audience == "kp"
                            else len(events) + 1
                        ),
                        "signal_id": signal_id,
                        "from": before.model_dump(mode="json") if before else None,
                        "to": after.model_dump(mode="json") if after else None,
                        "created_at": batch["created_at"],
                    }
                )
            previous = current
        return events[-20:]

    @staticmethod
    def _public_event_id(batch_id: str, signal_id: str) -> str:
        digest = hashlib.sha256(f"{batch_id}:{signal_id}".encode()).hexdigest()[:16]
        return f"signal-event-{digest}"

    @staticmethod
    def _signature(
        item: ConsequenceSignalProjection | None,
    ) -> tuple[Any, ...] | None:
        if item is None:
            return None
        return (
            item.severity,
            item.label,
            item.description,
            item.current_value,
            item.active,
        )


__all__ = ["ConsequenceSignalService"]
