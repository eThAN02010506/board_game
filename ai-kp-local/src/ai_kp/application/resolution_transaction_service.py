"""Atomic settlement across scenario state and installed ruleset state."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.application.scenario_effect_service import ScenarioEffectService
from ai_kp.platform.resolution.authority_basis import KernelAuthorityBasis
from ai_kp.platform.resolution.contracts import ResolutionPreview
from ai_kp.platform.sessions.models import AuthenticatedMember


class ResolutionTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    transaction_id: str = Field(min_length=1, max_length=320)
    run_id: str
    action_id: str
    expected_run_version: int = Field(ge=0)
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome: str = Field(min_length=1, max_length=120)
    phase: Literal["committed"] = "committed"


class ResolutionTransactionService:
    """Commit every deterministic effect or roll the complete batch back."""

    def __init__(self, repo: Any):
        self.repo = repo

    def commit(
        self,
        *,
        proposal: dict[str, Any],
        preview: ResolutionPreview,
        authority_basis: KernelAuthorityBasis,
        run_id: str,
        outcome: str,
        identity: AuthenticatedMember,
    ) -> dict[str, Any]:
        preview.commands_for_outcome(outcome)
        transaction = ResolutionTransaction(
            transaction_id=(
                f"action:{preview.action_id}:v{preview.run_version}:{outcome}"
            ),
            run_id=run_id,
            action_id=preview.action_id,
            expected_run_version=preview.run_version,
            preview_hash=preview.preview_hash,
            outcome=outcome,
        )
        self.repo.begin_immediate()
        self.repo.begin_scenario_command_batch()
        try:
            action = self.repo.get_player_action(str(preview.action_id))
            if (
                str(action.get("proposal_id") or "") != str(proposal["id"])
                or str(action.get("campaign_id") or "")
                != str(proposal["campaign_id"])
            ):
                raise ValueError(
                    "Kernel settlement action is not bound to its proposal and campaign"
                )
            actor_id = str(action.get("pc_id") or action.get("member_id") or "")
            if not actor_id:
                raise ValueError("Kernel settlement action has no authoritative actor")
            batch = self.repo.commit_action_scenario_batch(
                run_id=run_id,
                idempotency_key=transaction.transaction_id,
                preview=preview,
                authority_basis=authority_basis,
                outcome=outcome,
                actor_id=actor_id,
            )
            batch.update(ScenarioEffectService(self.repo).apply_batch(
                batch,
                identity,
                campaign_id=str(proposal["campaign_id"]),
                fallback_actor_id=proposal.get("pc_id"),
            ))
            plan = self.repo.get_kernel_plan_for_action(str(preview.action_id))
            if plan is not None:
                batch["kernel_plan"] = self.repo.finish_kernel_plan_step(
                    str(preview.action_id), outcome=outcome
                )
            batch["resolution_transaction"] = transaction.model_dump(mode="json")
            self.repo.finish_scenario_command_batch()
            return batch
        except Exception:
            self.repo.rollback_scenario_command_batch()
            raise


__all__ = ["ResolutionTransaction", "ResolutionTransactionService"]
