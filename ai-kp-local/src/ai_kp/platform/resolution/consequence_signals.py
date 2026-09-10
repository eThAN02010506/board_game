"""Deterministic and visibility-safe projections of generic world pressure."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ai_kp.platform.resolution.contracts import ScenarioContract, ScenarioSnapshot
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler

SignalAudience = Literal["table", "kp"]


class ConsequenceSignalProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: str
    title: str
    display_mode: Literal["exact", "stage", "narrative"]
    severity: int
    label: str
    description: str
    current_value: Any = None
    source_path: str | None = None
    band_id: str | None = None
    active: bool = True


class ConsequenceSignalProjector:
    """Project committed state; never infer, narrate, or mutate consequences."""

    def __init__(self, contract: ScenarioContract):
        self.contract = contract
        self.kernel = ActionResolutionKernel.from_contract(contract)
        self._contract_hash = ScenarioContractCompiler.contract_hash(contract)

    def project(
        self,
        snapshot: ScenarioSnapshot,
        *,
        audience: SignalAudience,
    ) -> tuple[ConsequenceSignalProjection, ...]:
        projections: list[ConsequenceSignalProjection] = []
        query = self.kernel.query_snapshot(snapshot)
        for signal in sorted(
            self.contract.consequence_signals, key=lambda item: item.signal_id
        ):
            if audience == "table" and signal.visibility != "table":
                continue
            selected = next(
                (
                    band
                    for band in sorted(
                        signal.bands,
                        key=lambda item: (-item.priority, item.band_id),
                    )
                    if query.conditions_satisfied(band.all_conditions)
                ),
                None,
            )
            if audience == "table" and (
                selected is None or not selected.player_visible
            ):
                continue

            found = False
            value: Any = None
            if signal.source_path is not None:
                found, value = query.state_value(signal.source_path)
            if audience == "table":
                if signal.display_mode == "exact" and (
                    not found or not isinstance(value, (str, int, float, bool))
                ):
                    continue
                projections.append(
                    ConsequenceSignalProjection(
                        signal_id=self._public_signal_id(signal.signal_id),
                        title=signal.public_title,
                        display_mode=signal.display_mode,
                        severity=selected.severity,
                        label=selected.public_label,
                        description=(
                            selected.public_description or signal.public_summary
                        ),
                        current_value=self._public_exact_value(
                            value,
                            enabled=signal.display_mode == "exact" and found,
                        ),
                    )
                )
                continue

            projections.append(
                ConsequenceSignalProjection(
                    signal_id=signal.signal_id,
                    title=signal.title,
                    display_mode=signal.display_mode,
                    severity=selected.severity if selected is not None else 0,
                    label=(
                        (selected.public_label or selected.band_id)
                        if selected is not None
                        else "inactive"
                    ),
                    description=(
                        selected.public_description if selected is not None else ""
                    ),
                    current_value=value if found else None,
                    source_path=signal.source_path,
                    band_id=selected.band_id if selected is not None else None,
                    active=selected is not None,
                )
            )
        for pressure in sorted(
            self.contract.pressure_tracks, key=lambda item: item.pressure_id
        ):
            if audience == "table" and pressure.visibility != "table":
                continue
            current = snapshot.clocks.get(pressure.clock_id)
            selected = next(
                (
                    stage
                    for stage in reversed(pressure.stages)
                    if current is not None and current >= stage.threshold
                ),
                None,
            )
            if audience == "table" and selected is None:
                continue
            severity = (
                pressure.stages.index(selected) + 1 if selected is not None else 0
            )
            projections.append(
                ConsequenceSignalProjection(
                    signal_id=(
                        self._public_signal_id(f"pressure:{pressure.pressure_id}")
                        if audience == "table"
                        else f"pressure:{pressure.pressure_id}"
                    ),
                    title=pressure.title,
                    display_mode=pressure.display_mode,
                    severity=severity,
                    label=(selected.public_label if selected is not None else "inactive"),
                    description=(
                        selected.public_description if selected is not None else ""
                    ),
                    current_value=(
                        current
                        if audience == "kp" or pressure.display_mode == "exact"
                        else None
                    ),
                    source_path=(
                        f"clocks.{pressure.clock_id}" if audience == "kp" else None
                    ),
                    band_id=(
                        selected.stage_id
                        if audience == "kp" and selected is not None
                        else None
                    ),
                    active=selected is not None,
                )
            )
        return tuple(projections)

    def _public_signal_id(self, signal_id: str) -> str:
        digest = hashlib.sha256(
            f"{self._contract_hash}:{signal_id}".encode()
        ).hexdigest()[:16]
        return f"signal-{digest}"

    @staticmethod
    def _public_exact_value(value: Any, *, enabled: bool) -> Any:
        if not enabled or not isinstance(value, (str, int, float, bool)):
            return None
        return value


__all__ = [
    "ConsequenceSignalProjection",
    "ConsequenceSignalProjector",
    "SignalAudience",
]
