"""Policy-controlled automatic materialization for world-expansion proposals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ai_kp.application.errors import ApplicationError
from ai_kp.application.ports.director import WorldExpansionDirector
from ai_kp.application.ports.repositories import TurnStore
from ai_kp.application.ports.world_expansion_materializations import (
    WorldExpansionMaterializationStore,
)
from ai_kp.application.turn_service import TurnService, WorldExpansionCommand
from ai_kp.application.world_expansion_materialization_service import (
    EncounterFact,
    MaterializeWorldExpansionCommand,
    WorldExpansionMaterializationService,
)
from ai_kp.director.turn_output import StructuredOutputError
from ai_kp.platform.sessions.models import AuthenticatedMember

AutomationLevel = Literal["conservative", "balanced", "ai_kp"]
AutoWorldExpansionStatus = Literal["materialized", "needs_attention", "failed"]
_RECOVERABLE_ERRORS = (
    ApplicationError,
    KeyError,
    PermissionError,
    RuntimeError,
    StructuredOutputError,
    ValueError,
)


class AutoWorldExpansionStore(
    TurnStore,
    WorldExpansionMaterializationStore,
    Protocol,
):
    pass


@dataclass(frozen=True)
class AutoWorldExpansionResult:
    status: AutoWorldExpansionStatus
    proposal: dict | None
    message: str
    materialization: dict | None = None
    policy: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "proposal": self.proposal,
            "materialization": self.materialization,
            "policy": self.policy,
            "message": self.message,
        }


class AutoWorldExpansionService:
    """Generate, approve and materialize only low-side-effect expansion candidates."""

    def __init__(self, repo: AutoWorldExpansionStore):
        self.repo = repo
        self.turns = TurnService(repo)

    async def create_and_maybe_materialize(
        self,
        command: WorldExpansionCommand,
        identity: AuthenticatedMember,
        director: WorldExpansionDirector,
        *,
        source_model: str,
    ) -> AutoWorldExpansionResult:
        proposal = await self.turns.create_world_expansion_proposal(
            command,
            identity,
            director,
            source_model=source_model,
        )
        return self.materialize_existing(proposal, identity)

    def materialize_existing(
        self,
        proposal: dict,
        identity: AuthenticatedMember,
    ) -> AutoWorldExpansionResult:
        if proposal.get("proposal_kind") != "world_expansion":
            return AutoWorldExpansionResult(
                status="needs_attention",
                proposal=proposal,
                message="只有世界补全 proposal 可自动 materialize。",
            )
        materialized = self.repo.get_world_expansion_materialization(str(proposal["id"]))
        if materialized is not None:
            return AutoWorldExpansionResult(
                status="materialized",
                proposal=proposal,
                materialization=materialized,
                message="世界补全已落地；本次请求复用已有 materialization。",
            )

        policy = self._policy_decision(proposal)
        self._record_policy(proposal, identity, policy)
        if not policy["allowed"]:
            return AutoWorldExpansionResult(
                status="needs_attention",
                proposal=proposal,
                policy=policy,
                message="世界补全已生成，但当前自动化策略要求人类 KP 审核。",
            )
        try:
            approved = (
                proposal
                if proposal.get("status") in {"approved", "overridden"}
                else self.turns.approve(
                    str(proposal["id"]),
                    str(proposal["campaign_id"]),
                    identity,
                    note="auto-kp approved world expansion",
                )
            )
            materialized_proposal = WorldExpansionMaterializationService(
                self.repo
            ).materialize(
                str(approved["id"]),
                identity,
                self._materialize_command(approved, policy),
            )
        except _RECOVERABLE_ERRORS as exc:
            failure_policy = {
                **policy,
                "allowed": False,
                "failure": str(exc)[:1000],
            }
            self._record_policy(proposal, identity, failure_policy)
            return AutoWorldExpansionResult(
                status="failed",
                proposal=self.repo.get_turn_proposal(str(proposal["id"])),
                policy=failure_policy,
                message=f"自动 materialize 世界补全失败：{exc}",
            )
        materialized = self.repo.get_world_expansion_materialization(
            str(materialized_proposal["id"])
        )
        return AutoWorldExpansionResult(
            status="materialized",
            proposal=materialized_proposal,
            materialization=materialized,
            policy=policy,
            message="世界补全已按自动化策略批准并落地。",
        )

    def _policy_decision(self, proposal: dict) -> dict[str, Any]:
        expansion = proposal["world_expansion"]
        run = self.repo.get_campaign_module_run(str(expansion["module_run_id"]))
        level = self._automation_level(run)
        candidate = expansion["candidate"]
        blockers: list[str] = []
        if level == "conservative":
            blockers.append("automation_level:conservative")
        if candidate.get("expansion_kind") != "environment":
            blockers.append("expansion_kind_requires_human_review")
        if candidate.get("confidence") == "low":
            blockers.append("low_confidence")
        if candidate.get("conflicts"):
            blockers.append("candidate_conflicts")
        if candidate.get("branch_plan") is not None:
            blockers.append("branch_plan_requires_contact_review")
        allowed = not blockers and level in {"balanced", "ai_kp"}
        return {
            "schema_version": "auto-world-expansion-policy.v1",
            "automation_level": level,
            "allowed": allowed,
            "blockers": blockers,
            "candidate": {
                "expansion_kind": candidate.get("expansion_kind"),
                "confidence": candidate.get("confidence"),
                "subject": candidate.get("subject"),
            },
        }

    def _materialize_command(
        self,
        proposal: dict,
        policy: dict[str, Any],
    ) -> MaterializeWorldExpansionCommand:
        candidate = proposal["world_expansion"]["candidate"]
        subject = str(candidate["subject"]).strip()
        text = str(candidate["proposal"]).strip()
        summary = str(proposal["public_narration"]).strip() or text
        return MaterializeWorldExpansionCommand(
            idempotency_key=f"auto-world:{proposal['id']}",
            summary=summary[:2000],
            happened_at=self.repo.get_campaign(str(proposal["campaign_id"])).get(
                "current_time"
            ),
            facts=(
                EncounterFact(
                    fact_type="canonical_fact",
                    subject=subject[:200],
                    predicate="存在或成立",
                    object_text=text[:4000],
                ),
            ),
            interaction_summary=(
                "Auto KP materialized an environment-only world expansion "
                f"under {policy['automation_level']} automation."
            ),
        )

    def _record_policy(
        self,
        proposal: dict,
        identity: AuthenticatedMember,
        policy: dict[str, Any],
    ) -> None:
        self.repo.add_proposal_action(
            str(proposal["id"]),
            "auto_world_expansion_policy",
            actor=f"kp:{identity.member_id}",
            note="automatic materialization policy decision",
            payload=policy,
        )

    def _automation_level(self, run: dict) -> AutomationLevel:
        level = str(run.get("automation_level") or "conservative")
        if level in {"conservative", "balanced", "ai_kp"}:
            return level  # type: ignore[return-value]
        return "conservative"
