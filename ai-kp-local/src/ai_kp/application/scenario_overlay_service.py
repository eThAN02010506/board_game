"""Application boundary for validated run-scoped scenario expansion overlays."""

from __future__ import annotations

from typing import Any, cast

from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.world_expansion_contract import (
    AutomationLevel,
    ExpansionRecords,
    WorldExpansionContractProposal,
    WorldExpansionContractValidator,
)
from ai_kp.rulesets import get_ruleset


class ScenarioOverlayService:
    def __init__(self, repo: Any):
        self.repo = repo

    def propose(
        self,
        run_id: str,
        proposal: WorldExpansionContractProposal,
        *,
        created_by_member_id: str | None,
    ) -> dict[str, Any]:
        # Serialize the authoritative run/binding/state decision with persistence.
        # Validation may be expensive, but it is deterministic and local.
        self.repo.begin_immediate()
        run = self.repo.get_campaign_module_run(run_id)
        if run.get("status") != "active":
            raise ValueError("Only an active module run accepts contract overlays")
        raw_level = str(run.get("automation_level") or "conservative")
        automation_level = cast(
            AutomationLevel,
            raw_level
            if raw_level in {"conservative", "balanced", "ai_kp"}
            else "conservative",
        )
        binding = self.repo.get_module_run_contract_binding(run_id)
        state = self.repo.get_scenario_run_state(run_id)
        ruleset = get_ruleset(binding["contract"].ruleset_id)
        validator = WorldExpansionContractValidator(
            ScenarioContractCompiler(ruleset.scenario_effect_catalog()),
            ruleset.scenario_check_catalog(),
        )
        decision = validator.validate(
            binding["contract"],
            proposal,
            automation_level=automation_level,
            trusted_dynamic_records=self._active_dynamic_records(run_id),
        )
        if proposal.base_state_version != int(state["state_version"]):
            decision = decision.model_copy(
                update={
                    "status": "rejected",
                    "blockers": (*decision.blockers, "base_state_version_mismatch"),
                    "merged_contract": None,
                    "merged_contract_hash": None,
                }
            )
        saved = self.repo.save_scenario_contract_overlay(
            run_id=run_id,
            proposal=proposal,
            decision=decision,
            status=("review_required" if decision.status == "auto_approved" else decision.status),
            created_by_member_id=created_by_member_id,
        )
        if decision.status == "auto_approved":
            return self.repo.activate_scenario_contract_overlay(
                str(saved["id"]), reviewed_by_member_id=created_by_member_id
            )
        return saved

    def approve(
        self, overlay_id: str, *, reviewed_by_member_id: str | None
    ) -> dict[str, Any]:
        # Review is not an escape hatch from deterministic release gates. Older
        # rows may predate stricter playability/provenance checks, so revalidate
        # the immutable proposal against the current effective contract while
        # holding the same write lock used by activation.
        self.repo.begin_immediate()
        overlay = self.repo.get_scenario_contract_overlay(overlay_id)
        if overlay["status"] == "active":
            return overlay
        if overlay["status"] != "review_required":
            raise ValueError("Only a reviewable overlay can be activated")
        run_id = str(overlay["run_id"])
        run = self.repo.get_campaign_module_run(run_id)
        if run.get("status") != "active":
            raise ValueError("Only an active module run accepts contract overlays")
        binding = self.repo.get_module_run_contract_binding(run_id)
        state = self.repo.get_scenario_run_state(run_id)
        proposal = overlay["proposal"]
        raw_level = str(run.get("automation_level") or "conservative")
        automation_level = cast(
            AutomationLevel,
            raw_level
            if raw_level in {"conservative", "balanced", "ai_kp"}
            else "conservative",
        )
        ruleset = get_ruleset(binding["contract"].ruleset_id)
        decision = WorldExpansionContractValidator(
            ScenarioContractCompiler(ruleset.scenario_effect_catalog()),
            ruleset.scenario_check_catalog(),
        ).validate(
            binding["contract"],
            proposal,
            automation_level=automation_level,
            trusted_dynamic_records=self._active_dynamic_records(run_id),
        )
        if proposal.base_state_version != int(state["state_version"]):
            raise ValueError("Scenario state changed; regenerate the overlay")
        if decision.status == "rejected" or decision.merged_contract is None:
            detail = ", ".join(decision.blockers) or "current release gates"
            raise ValueError(
                "Scenario overlay is no longer release-ready: " + detail
            )
        if decision.merged_contract_hash != overlay["merged_contract_hash"]:
            raise ValueError("Scenario overlay changed under current validation")
        return self.repo.activate_scenario_contract_overlay(
            overlay_id, reviewed_by_member_id=reviewed_by_member_id
        )

    def _active_dynamic_records(self, run_id: str) -> tuple[ExpansionRecords, ...]:
        return tuple(
            item["proposal"].records
            for item in self.repo.list_scenario_contract_overlays(run_id)
            if item["status"] == "active"
        )


__all__ = ["ScenarioOverlayService"]
