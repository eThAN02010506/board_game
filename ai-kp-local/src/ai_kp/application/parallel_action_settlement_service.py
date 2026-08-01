"""Unified settlement for multiple submitted player actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ai_kp.application.action_adjudication_service import ActionAdjudicationService
from ai_kp.application.auto_turn_service import RECOVERABLE_AUTO_TURN_ERRORS
from ai_kp.application.ports.director import KpDirector
from ai_kp.application.ports.repositories import TurnStore
from ai_kp.application.turn_service import KpTurnCommand, ManualProposalCommand, TurnService
from ai_kp.platform.sessions.models import AuthenticatedMember

ParallelSettlementStatus = Literal[
    "approved",
    "awaiting_confirmation",
    "needs_attention",
    "failed",
]


@dataclass(frozen=True)
class ParallelActionSettlementCommand:
    action_ids: tuple[str, ...]
    auto_approve: bool = True
    player_confirmation: bool = False


@dataclass(frozen=True)
class ParallelActionSettlementResult:
    status: ParallelSettlementStatus
    proposal: dict | None
    actions: list[dict]
    message: str
    adjudications: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "proposal": self.proposal,
            "actions": self.actions,
            "message": self.message,
            "adjudications": self.adjudications,
        }


class ParallelActionSettlementService:
    """Create one auditable proposal for concurrent player intent."""

    def __init__(self, repo: TurnStore):
        self.repo = repo
        self.turns = TurnService(repo)

    async def settle(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: ParallelActionSettlementCommand,
        director: KpDirector,
        *,
        source_model: str,
    ) -> ParallelActionSettlementResult:
        actions = self._claimable_actions(campaign_id, identity, command.action_ids)
        if len(actions) < 2:
            raise ValueError("Parallel settlement requires at least two actions")
        action_text = self._combined_action_text(actions)
        try:
            proposal = await self.turns.create_ai_proposal(
                KpTurnCommand(
                    campaign_id=campaign_id,
                    player_action=action_text,
                    active_spoiler_tags=(),
                ),
                identity,
                director,
                source_model=source_model,
            )
        except RECOVERABLE_AUTO_TURN_ERRORS as exc:
            proposal = self.turns.create_manual_proposal(
                campaign_id,
                identity,
                ManualProposalCommand(
                    player_action=action_text,
                    public_narration=(
                        "大家的行动被合并为一个并行回合处理；当前没有立即写入新的"
                        "隐藏事实或角色状态。"
                    ),
                    kp_notes=f"多人并行自动结算降级：{str(exc)[:1000]}",
                    action_ruling={
                        "goal": "统一处理多人并行动作",
                        "method": "保守聚合裁定",
                        "target": "当前场景",
                        "feasibility": "impossible",
                        "resolution": "no_roll",
                        "reason": "模型不可用或输出不稳定，不能安全地合并执行多人行动。",
                        "maximum_effect": "不执行行动，不写入世界事实或角色状态。",
                        "alternative": "请各玩家补充目标、手段或对白后重新裁定。",
                    },
                    source_model="auto-kp-fallback",
                ),
            )
        self._link_actions(actions, proposal)
        self.repo.add_proposal_action(
            str(proposal["id"]),
            "parallel_action_batch",
            actor=f"kp:{identity.member_id}",
            note="multiple submitted actions settled as one turn proposal",
            payload={"action_ids": [item["id"] for item in actions]},
        )
        proposal = self.repo.get_turn_proposal(str(proposal["id"]))
        if command.player_confirmation:
            fallback = proposal.get("source_model") == "auto-kp-fallback"
            adjudications = [
                ActionAdjudicationService(self.repo).create(
                    action,
                    proposal,
                    source_model="auto-kp-fallback" if fallback else source_model,
                    source_error="多人裁定未通过模型校验" if fallback else None,
                    enforce_precheck=False,
                )
                for action in actions
            ]
            return ParallelActionSettlementResult(
                status="awaiting_confirmation",
                proposal=proposal,
                actions=self._refresh_actions(actions),
                adjudications=adjudications,
                message="多人并行动作已统一裁定，等待各玩家分别确认。",
            )
        if not command.auto_approve or self._automation_level(campaign_id) == "conservative":
            return ParallelActionSettlementResult(
                status="needs_attention",
                proposal=proposal,
                actions=self._refresh_actions(actions),
                message="多人并行动作已合并为草稿，等待人类 KP 审批。",
            )
        if proposal["proposed_checks"]:
            return ParallelActionSettlementResult(
                status="needs_attention",
                proposal=proposal,
                actions=self._refresh_actions(actions),
                message="多人并行草稿包含检定；需要先由 KP 拆分或确认检定归属。",
            )
        try:
            approved = self.turns.approve(
                str(proposal["id"]),
                campaign_id,
                identity,
                note="auto-kp approved parallel action batch",
            )
        except RECOVERABLE_AUTO_TURN_ERRORS as exc:
            return ParallelActionSettlementResult(
                status="failed",
                proposal=proposal,
                actions=self._refresh_actions(actions),
                message=f"多人并行自动批准失败：{exc}",
            )
        return ParallelActionSettlementResult(
            status="approved",
            proposal=approved,
            actions=self._refresh_actions(actions),
            message="多人并行动作已统一结算。",
        )

    def _claimable_actions(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        action_ids: tuple[str, ...],
    ) -> list[dict]:
        unique_ids = tuple(dict.fromkeys(item.strip() for item in action_ids if item.strip()))
        if len(unique_ids) != len(action_ids):
            raise ValueError("Parallel action IDs must be unique and non-empty")
        if len(unique_ids) > 12:
            raise ValueError("Parallel settlement supports at most 12 actions")
        return [
            self.repo.require_submitted_player_action(
                action_id,
                campaign_id,
                identity.session_id,
            )
            for action_id in unique_ids
        ]

    def _link_actions(self, actions: list[dict], proposal: dict) -> None:
        for action in actions:
            self.repo.link_player_action_to_proposal(
                str(action["id"]),
                str(proposal["id"]),
                str(proposal["campaign_id"]),
            )

    def _refresh_actions(self, actions: list[dict]) -> list[dict]:
        return [self.repo.get_player_action(str(item["id"])) for item in actions]

    def _combined_action_text(self, actions: list[dict]) -> str:
        lines = [
            "多人并行动作统一结算：",
            *(
                f"- {action.get('pc_id') or action['member_id']}: {action['action_text']}"
                for action in actions
            ),
        ]
        return "\n".join(lines)[:4000]

    def _automation_level(self, campaign_id: str) -> str:
        run = self.repo.get_active_campaign_module_run(campaign_id)
        if run is None:
            return "ai_kp"
        level = str(run.get("automation_level") or "conservative")
        return level if level in {"conservative", "balanced", "ai_kp"} else "conservative"
