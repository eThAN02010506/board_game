"""Shared validation for a contract binding and its persisted scenario snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ai_kp.application.errors import ConflictError
from ai_kp.platform.resolution.authority_basis import KernelAuthorityBasis
from ai_kp.platform.resolution.contracts import ScenarioContract, ScenarioSnapshot

ScenarioStatePolicy = Literal["require", "initialize", "ephemeral"]


@dataclass(frozen=True)
class ScenarioAuthorityContext:
    """One immutable view of the run, contract binding, and scenario state."""

    run_id: str
    campaign_id: str
    module_id: str
    module_run_version: int
    run_status: str
    director_control_mode: str
    active_spoiler_tags: tuple[str, ...]
    contract_version_id: str
    contract_hash: str
    state_version: int
    state_persisted: bool
    contract: ScenarioContract
    snapshot: ScenarioSnapshot

    def authority_identity(self) -> tuple[Any, ...]:
        """Return every value whose drift invalidates work based on this context."""

        return (
            self.run_id,
            self.campaign_id,
            self.module_id,
            self.module_run_version,
            self.run_status,
            self.director_control_mode,
            self.active_spoiler_tags,
            self.contract_version_id,
            self.contract_hash,
            self.state_version,
            self.state_persisted,
            self.contract,
            self.snapshot,
        )

    def kernel_authority_basis(self, preview_hash: str) -> KernelAuthorityBasis:
        """Freeze this coherent context for a deterministic commit artifact."""

        return KernelAuthorityBasis(
            run_id=self.run_id,
            contract_version_id=self.contract_version_id,
            contract_hash=self.contract_hash,
            state_version=self.state_version,
            preview_hash=preview_hash,
        )


def validate_bound_scenario_state(
    *,
    run_id: str,
    binding: dict[str, Any],
    state: dict[str, Any],
) -> tuple[ScenarioContract, ScenarioSnapshot, int]:
    """Return coherent authority values or reject mismatched persisted metadata."""

    bound_version_id = binding.get("contract_version_id")
    state_version_id = state.get("contract_version_id")
    if (
        not isinstance(bound_version_id, str)
        or not bound_version_id
        or not isinstance(state_version_id, str)
        or state_version_id != bound_version_id
    ):
        raise ConflictError("Scenario state is bound to a different contract")
    contract = binding["contract"]
    snapshot = state["snapshot"]
    version = int(state["state_version"])
    if snapshot.run_id != run_id or snapshot.run_version != version:
        raise ConflictError("Scenario state metadata does not match its snapshot")
    if (
        snapshot.contract_id != contract.contract_id
        or snapshot.scenario_version != contract.source_version
    ):
        raise ConflictError("Scenario snapshot does not match the effective contract")
    return contract, snapshot, version


def load_scenario_authority_context(
    repo: Any,
    run_id: str,
    *,
    state_policy: ScenarioStatePolicy,
) -> ScenarioAuthorityContext:
    """Load and validate one coherent scenario basis under an explicit state policy."""

    run = repo.get_campaign_module_run(run_id)
    if str(run.get("id")) != run_id:
        raise ConflictError("Scenario run identity changed while loading authority")
    binding = repo.get_module_run_contract_binding(run_id)
    if str(binding.get("run_id")) != run_id:
        raise ConflictError("Scenario contract binding belongs to a different run")
    contract_version_id = str(binding.get("contract_version_id") or "")
    contract_hash = str(binding.get("contract_hash") or "").strip().casefold()
    if not contract_version_id:
        raise ConflictError("Scenario contract binding has no version identity")
    if len(contract_hash) != 64 or any(
        character not in "0123456789abcdef" for character in contract_hash
    ):
        raise ConflictError("Scenario contract binding has an invalid content hash")

    state_persisted = True
    if state_policy == "initialize":
        state = repo.initialize_scenario_run_state(run_id)
    else:
        try:
            state = repo.get_scenario_run_state(run_id)
        except KeyError:
            if state_policy == "require":
                raise ConflictError(
                    "Scenario authority requires an initialized scenario state"
                ) from None
            contract = binding["contract"]
            snapshot = contract.initial_snapshot(run_id)
            state = {
                "contract_version_id": contract_version_id,
                "state_version": snapshot.run_version,
                "snapshot": snapshot,
            }
            state_persisted = False

    contract, snapshot, state_version = validate_bound_scenario_state(
        run_id=run_id,
        binding=binding,
        state=state,
    )
    campaign_id = str(run.get("campaign_id") or "")
    module_id = str(run.get("module_id") or "")
    if not campaign_id or not module_id:
        raise ConflictError("Scenario run has incomplete campaign or module identity")
    return ScenarioAuthorityContext(
        run_id=run_id,
        campaign_id=campaign_id,
        module_id=module_id,
        module_run_version=int(run["version"]),
        run_status=str(run["status"]),
        director_control_mode=str(run.get("director_control_mode") or ""),
        active_spoiler_tags=tuple(str(tag) for tag in run.get("active_spoiler_tags") or ()),
        contract_version_id=contract_version_id,
        contract_hash=contract_hash,
        state_version=state_version,
        state_persisted=state_persisted,
        contract=contract,
        snapshot=snapshot,
    )


def load_active_scenario_authority_context(
    repo: Any,
    campaign_id: str,
    *,
    state_policy: ScenarioStatePolicy,
) -> ScenarioAuthorityContext | None:
    """Load the campaign's active bound scenario, or return None when unavailable."""

    run = repo.get_active_campaign_module_run(campaign_id)
    if run is None:
        return None
    try:
        context = load_scenario_authority_context(
            repo,
            str(run["id"]),
            state_policy=state_policy,
        )
    except KeyError:
        return None
    if context.campaign_id != campaign_id or context.run_status != "active":
        raise ConflictError("Active scenario authority changed while loading")
    return context


def revalidate_scenario_authority_context(
    repo: Any,
    expected: ScenarioAuthorityContext,
) -> ScenarioAuthorityContext:
    """Reload an existing basis and reject any authority or evidence-scope drift."""

    current = load_scenario_authority_context(
        repo,
        expected.run_id,
        state_policy="require" if expected.state_persisted else "ephemeral",
    )
    if current.authority_identity() != expected.authority_identity():
        raise ConflictError("Scenario authority changed; rebuild the current context")
    return current


__all__ = [
    "ScenarioAuthorityContext",
    "ScenarioStatePolicy",
    "load_active_scenario_authority_context",
    "load_scenario_authority_context",
    "revalidate_scenario_authority_context",
    "validate_bound_scenario_state",
]
