"""Server-side automation for player-only turn advancement."""

from dataclasses import dataclass
from typing import Any, Literal

from ai_kp.application.action_adjudication_service import ActionAdjudicationService
from ai_kp.application.check_consequence_service import (
    CheckConsequenceService,
    GenerateCheckConsequenceCommand,
)
from ai_kp.application.errors import UpstreamServiceError
from ai_kp.application.ports.director import CheckConsequenceDirector, KpDirector
from ai_kp.application.ports.repositories import TurnStore
from ai_kp.application.turn_service import KpTurnCommand, ManualProposalCommand, TurnService
from ai_kp.director.turn_output import StructuredOutputError
from ai_kp.platform.resolution import (
    HIDDEN_CHECK_PUBLIC_NARRATION,
    build_check_consequence_snapshot,
)
from ai_kp.platform.sessions.models import AuthenticatedMember

AutoTurnStatus = Literal[
    "completed",
    "awaiting_roll",
    "awaiting_confirmation",
    "needs_attention",
    "failed",
]
AutomationLevel = Literal["conservative", "balanced", "ai_kp"]
RECOVERABLE_AUTO_TURN_ERRORS = (
    RuntimeError,
    StructuredOutputError,
    UpstreamServiceError,
)


@dataclass(frozen=True)
class AutoTurnResult:
    status: AutoTurnStatus
    player_action: dict
    proposal: dict | None = None
    checks: list[dict] = ()
    adjudication: dict | None = None
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "player_action": self.player_action,
            "proposal": self.proposal,
            "checks": list(self.checks),
            "adjudication": self.adjudication,
            "message": self.message,
        }


class AutoTurnService:
    """Advance a player action with an internal KP identity and safe fallbacks."""

    def __init__(self, repo: TurnStore):
        self.repo = repo
        self.turns = TurnService(repo)

    async def advance_player_action(
        self,
        action_id: str,
        *,
        director: KpDirector,
        source_model: str,
    ) -> AutoTurnResult:
        action = self.repo.get_player_action(action_id)
        automation_level = self._automation_level(str(action["campaign_id"]))
        if automation_level == "conservative":
            return AutoTurnResult(
                status="needs_attention",
                player_action=action,
                message="当前为保守模式：行动已提交，等待人类 KP 审批。",
            )
        identity = self._system_kp_identity(action)
        source_error: str | None = None
        try:
            proposal = await self.turns.create_ai_proposal(
                KpTurnCommand(
                    campaign_id=str(action["campaign_id"]),
                    player_action=str(action["action_text"]),
                    player_action_id=action_id,
                    pc_id=action.get("pc_id"),
                    location=action.get("location"),
                    map_id=action.get("map_id"),
                    active_spoiler_tags=(),
                ),
                identity,
                director,
                source_model=source_model,
            )
        except RECOVERABLE_AUTO_TURN_ERRORS as exc:
            proposal = self._fallback_proposal(action, identity, reason=str(exc))
            source_error = str(exc)

        if proposal.get("proposal_kind") != "standard":
            return AutoTurnResult(
                status="needs_attention",
                player_action=self.repo.get_player_action(action_id),
                proposal=proposal,
                message="自动 KP 生成了需要人工确认的特殊草稿。",
            )
        adjudication = ActionAdjudicationService(self.repo).create(
            action,
            proposal,
            source_model=source_model if source_error is None else "auto-kp-fallback",
            source_error=source_error,
        )
        return AutoTurnResult(
            status="awaiting_confirmation",
            player_action=self.repo.get_player_action(action_id),
            proposal=self.repo.get_turn_proposal(str(proposal["id"])),
            adjudication=adjudication,
            message="AI KP 已给出初步裁定；请确认、改选技能或补充行动。",
        )

    async def advance_after_check(
        self,
        check_id: str,
        *,
        director: CheckConsequenceDirector,
        source_model: str,
    ) -> AutoTurnResult | None:
        check = self.repo.get_skill_check(check_id)
        action_id = check.get("player_action_id")
        if not action_id:
            return None
        action = self.repo.get_player_action(str(action_id))
        automation_level = self._automation_level(str(action["campaign_id"]))
        if automation_level == "conservative":
            return AutoTurnResult(
                status="needs_attention",
                player_action=action,
                checks=self.repo.list_skill_checks_for_action(str(action_id)),
                message="当前为保守模式：检定后果等待人类 KP 审批。",
            )
        if action["status"] != "reviewed":
            return None
        checks = self.repo.list_skill_checks_for_action(str(action_id))
        if not checks or any(item["status"] == "requested" for item in checks):
            return AutoTurnResult(
                status="awaiting_roll",
                player_action=action,
                checks=checks,
                message="仍有检定等待玩家完成。",
            )
        opposed = self.repo.list_opposed_checks_for_action(str(action_id))
        if any(item["status"] == "pending" for item in opposed):
            return AutoTurnResult(
                status="awaiting_roll",
                player_action=action,
                checks=checks,
                message="仍有对抗检定等待裁决。",
            )

        identity = self._system_kp_identity(action)
        try:
            proposal = await CheckConsequenceService(self.repo).generate(
                GenerateCheckConsequenceCommand(check_id=check_id),
                identity,
                director,
                source_model=source_model,
            )
        except RECOVERABLE_AUTO_TURN_ERRORS as exc:
            proposal = self._fallback_consequence(
                action,
                identity,
                checks=checks,
                reason=str(exc),
            )
        try:
            approved = self.turns.approve(
                str(proposal["id"]),
                str(action["campaign_id"]),
                identity,
                note="auto-kp approved check consequence",
            )
        except RECOVERABLE_AUTO_TURN_ERRORS as exc:
            return AutoTurnResult(
                status="failed",
                player_action=self.repo.get_player_action(str(action_id)),
                proposal=proposal,
                checks=checks,
                message=f"自动批准检定后果失败：{exc}",
            )
        return AutoTurnResult(
            status="completed",
            player_action=self.repo.get_player_action(str(action_id)),
            proposal=approved,
            checks=self.repo.list_skill_checks_for_action(str(action_id)),
            message="检定后果已由自动 KP 完成。",
        )

    def _system_kp_identity(self, action: dict) -> AuthenticatedMember:
        row = self.repo.connection.execute(
            """
            SELECT id, session_id, campaign_id, display_name
            FROM session_members
            WHERE session_id = ? AND campaign_id = ? AND role = 'kp'
              AND revoked_at IS NULL
            ORDER BY joined_at, id
            LIMIT 1
            """,
            (action["session_id"], action["campaign_id"]),
        ).fetchone()
        if row is None:
            raise ValueError("No active KP member exists for automatic advancement")
        return AuthenticatedMember(
            member_id=str(row["id"]),
            session_id=str(row["session_id"]),
            campaign_id=str(row["campaign_id"]),
            role="kp",
            display_name=f"{row['display_name']} · Auto KP",
            pc_id=None,
        )

    def _automation_level(self, campaign_id: str) -> AutomationLevel:
        run = self.repo.get_active_campaign_module_run(campaign_id)
        if run is None:
            return "ai_kp"
        level = str(run.get("automation_level") or "conservative")
        if level in {"conservative", "balanced", "ai_kp"}:
            return level
        return "conservative"

    def _fallback_proposal(
        self,
        action: dict,
        identity: AuthenticatedMember,
        *,
        reason: str,
    ) -> dict:
        text = str(action["action_text"]).strip()
        narration = "AI KP 暂时无法可靠裁定这个行动，尚未执行任何结果。"
        return self.turns.create_manual_proposal(
            str(action["campaign_id"]),
            identity,
            ManualProposalCommand(
                player_action=text,
                player_action_id=str(action["id"]),
                pc_id=action.get("pc_id"),
                public_narration=narration,
                kp_notes=f"自动 KP 降级裁定：{reason[:1000]}",
                action_ruling={
                    "goal": text[:500] or "继续行动",
                    "method": "等待玩家补充",
                    "target": "当前场景",
                    "feasibility": "impossible",
                    "resolution": "no_roll",
                    "reason": "模型不可用或输出未通过校验，不能安全地假定行动可行。",
                    "maximum_effect": "不执行行动，不写入世界事实或角色状态。",
                    "alternative": "补充目标、手段或角色对白，然后请 AI 重新裁定。",
                },
                source_model="auto-kp-fallback",
            ),
        )

    def _fallback_consequence(
        self,
        action: dict,
        identity: AuthenticatedMember,
        *,
        checks: list[dict],
        reason: str,
    ) -> dict:
        visible = [check for check in checks if check.get("visibility") != "blind"]
        if not visible:
            narration = HIDDEN_CHECK_PUBLIC_NARRATION
        else:
            passed = sum(1 for check in visible if check.get("passed") is True)
            total = len(visible)
            narration = (
                f"检定结果已结算：{passed}/{total} 项达成。"
                " 场景按这个结果继续推进；你可以据此声明下一步行动。"
            )
        action_ruling = {
            "goal": str(action["action_text"])[:500] or "结算检定后果",
            "method": "根据已验证的掷骰结果结算",
            "target": "当前检定批次",
            "feasibility": "possible",
            "resolution": "automatic",
            "reason": "模型不可用或输出不稳定时，使用不写入额外世界状态的检定摘要。",
            "maximum_effect": "仅产生公开检定摘要，不写入隐藏事实或角色状态。",
            "alternative": "等待人类 KP 复核可获得更具体后果。",
        }
        proposal = self.repo.create_turn_proposal(
            campaign_id=str(action["campaign_id"]),
            pc_id=action.get("pc_id"),
            player_action=str(action["action_text"]),
            public_narration=narration,
            kp_notes=f"自动 KP 检定后果降级裁定：{reason[:1000]}",
            proposed_checks=[],
            proposed_events=[],
            proposed_memories=[],
            proposed_npc_updates=[],
            proposed_map_moves=[],
            proposed_facts=[],
            source_model="auto-kp-fallback",
        )
        self.repo.add_proposal_action(
            str(proposal["id"]),
            "action_ruling",
            actor="system",
            note="auto-kp fallback check consequence ceiling",
            payload=action_ruling,
        )
        snapshot = build_check_consequence_snapshot(
            checks,
            self.repo.list_opposed_checks_for_action(str(action["id"])),
        )
        self.repo.attach_check_consequence_basis(
            str(proposal["id"]),
            origin_proposal_id=str(action["proposal_id"]),
            player_action_id=str(action["id"]),
            check_ids=[str(item["id"]) for item in checks],
            result_fingerprint=str(snapshot["result_fingerprint"]),
        )
        return self.repo.get_turn_proposal(str(proposal["id"]))


__all__ = ["AutoTurnResult", "AutoTurnService"]
