"""Server-side automation for player-only turn advancement."""

from dataclasses import dataclass
from typing import Any, Literal, cast

from ai_kp.application.action_adjudication_service import ActionAdjudicationService
from ai_kp.application.check_consequence_service import (
    CheckConsequenceService,
    GenerateCheckConsequenceCommand,
)
from ai_kp.application.errors import UpstreamServiceError
from ai_kp.application.kernel_action_service import KernelActionService
from ai_kp.application.ports.director import (
    CheckConsequenceDirector,
    KernelDirector,
    KpDirector,
)
from ai_kp.application.ports.repositories import TurnStore
from ai_kp.application.turn_service import KpTurnCommand, ManualProposalCommand, TurnService
from ai_kp.director.errors import CampaignAiCallCancelledError
from ai_kp.director.turn_output import StructuredOutputError
from ai_kp.platform.resolution import (
    HIDDEN_CHECK_PUBLIC_NARRATION,
    build_check_consequence_snapshot,
    exact_kernel_outcome,
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
        skip_unbound_tabletop: bool = False,
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
        kernel_actions = KernelActionService(self.repo)
        supports_kernel = all(
            hasattr(director, method)
            for method in (
                "interpret_tabletop_turn",
                "respond_tabletop_turn",
                "select_kernel_action",
                "author_kernel_world_expansion",
                "narrate_kernel_action",
            )
        )
        bound_context = kernel_actions.bound_context(str(action["campaign_id"]))
        if bound_context is not None and supports_kernel:
            kernel_director = cast(KernelDirector, director)
            try:
                proposal, _preview = await kernel_actions.prepare(
                    action,
                    identity,
                    kernel_director,
                    source_model=source_model,
                    profile=self._semantic_profile(),
                )
            except CampaignAiCallCancelledError:
                raise
            except RECOVERABLE_AUTO_TURN_ERRORS as exc:
                return self._fallback_action_result(
                    action,
                    identity,
                    reason=str(exc),
                )
            adjudication = ActionAdjudicationService(self.repo).create(
                action,
                proposal,
                source_model=f"kernel:{source_model}",
                enforce_precheck=False,
            )
            return AutoTurnResult(
                status="awaiting_confirmation",
                player_action=self.repo.get_player_action(action_id),
                proposal=self.repo.get_turn_proposal(str(proposal["id"])),
                adjudication=adjudication,
                message="规则内核已给出可复核裁定；请确认或改选合理技能。",
            )
        if supports_kernel and not skip_unbound_tabletop:
            kernel_director = cast(KernelDirector, director)
            try:
                proposal = await kernel_actions.prepare_unbound_tabletop(
                    action,
                    identity,
                    kernel_director,
                    source_model=source_model,
                )
            except CampaignAiCallCancelledError:
                raise
            except RECOVERABLE_AUTO_TURN_ERRORS as exc:
                return self._fallback_action_result(
                    action,
                    identity,
                    reason=str(exc),
                )
            if proposal is not None:
                adjudication = ActionAdjudicationService(self.repo).create(
                    action,
                    proposal,
                    source_model=f"tabletop:{source_model}",
                    enforce_precheck=False,
                )
                return AutoTurnResult(
                    status="awaiting_confirmation",
                    player_action=self.repo.get_player_action(action_id),
                    proposal=proposal,
                    adjudication=adjudication,
                    message=(
                        "通用桌面协议已给出可复核回应；"
                        "本轮不写入世界状态。"
                    ),
                )
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
        except CampaignAiCallCancelledError:
            raise
        except RECOVERABLE_AUTO_TURN_ERRORS as exc:
            return self._fallback_action_result(
                action,
                identity,
                reason=str(exc),
            )

        if proposal.get("proposal_kind") != "standard":
            return AutoTurnResult(
                status="needs_attention",
                player_action=self.repo.get_player_action(action_id),
                proposal=proposal,
                message="自动 KP 生成了需要人工确认的特殊草稿。",
            )
        if automation_level == "ai_kp":
            # Without a published scenario contract, the model may narrate a
            # useful fallback but cannot safely author durable world state. The
            # public turn history still preserves continuity; canonical effects
            # resume once the deterministic kernel is bound.
            proposal = self.repo.clear_draft_world_effects(str(proposal["id"]))
        adjudication = ActionAdjudicationService(self.repo).create(
            action,
            proposal,
            source_model=source_model,
        )
        return AutoTurnResult(
            status="awaiting_confirmation",
            player_action=self.repo.get_player_action(action_id),
            proposal=self.repo.get_turn_proposal(str(proposal["id"])),
            adjudication=adjudication,
            message="AI KP 已给出初步裁定；请确认、改选技能或补充行动。",
        )

    async def advance_unbound_tabletop(
        self,
        action_id: str,
        *,
        director: KpDirector,
        source_model: str,
    ) -> AutoTurnResult | None:
        """Run the generic conversation boundary before legacy world-gap logic."""

        action = self.repo.get_player_action(action_id)
        kernel_actions = KernelActionService(self.repo)
        if kernel_actions.bound_context(str(action["campaign_id"])) is not None:
            return None
        required_methods = (
            "interpret_tabletop_turn",
            "respond_tabletop_turn",
            "select_kernel_action",
            "author_kernel_world_expansion",
            "narrate_kernel_action",
        )
        if not all(hasattr(director, method) for method in required_methods):
            return None
        identity = self._system_kp_identity(action)
        try:
            proposal = await kernel_actions.prepare_unbound_tabletop(
                action,
                identity,
                cast(KernelDirector, director),
                source_model=source_model,
            )
        except CampaignAiCallCancelledError:
            raise
        except RECOVERABLE_AUTO_TURN_ERRORS as exc:
            return self._fallback_action_result(
                action,
                identity,
                reason=str(exc),
            )
        if proposal is None:
            return None
        adjudication = ActionAdjudicationService(self.repo).create(
            action,
            proposal,
            source_model=f"tabletop:{source_model}",
            enforce_precheck=False,
        )
        return AutoTurnResult(
            status="awaiting_confirmation",
            player_action=self.repo.get_player_action(action_id),
            proposal=proposal,
            adjudication=adjudication,
            message="通用桌面协议已给出可复核回应；本轮不写入世界状态。",
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
        pushed_parent_ids = {
            str(item.get("pushed_from_check_id"))
            for item in checks
            if item.get("pushed_from_check_id")
        }
        unresolved_push_choices = [
            item
            for item in checks
            if str(item.get("id")) not in pushed_parent_ids
            and item.get("passed") is False
            and item.get("allow_push") is True
        ]
        if unresolved_push_choices:
            return AutoTurnResult(
                status="awaiting_confirmation",
                player_action=action,
                checks=checks,
                message=(
                    "检定失败；由掷骰玩家选择孤注一掷，或接受已公开的普通失败后果。"
                ),
            )

        identity = self._system_kp_identity(action)
        origin = self.repo.get_turn_proposal(str(action["proposal_id"]))
        kernel_payload = KernelActionService.kernel_payload(origin)
        if kernel_payload is not None:
            preview_payload = kernel_payload.get("preview")
            outcome = exact_kernel_outcome(
                preview_payload if isinstance(preview_payload, dict) else {},
                checks,
            )
            kernel_actions = KernelActionService(self.repo)
            batch = kernel_actions.commit(
                origin, outcome=outcome, identity=identity
            )
            proposal = self._fallback_consequence(
                action,
                identity,
                checks=checks,
                reason=f"kernel-authority:{outcome}",
            )
            approved = self.turns.approve(
                str(proposal["id"]),
                str(action["campaign_id"]),
                identity,
                note=f"kernel committed verified {outcome} command batch",
                override_public_narration=(
                    self._pushed_failure_narration(checks)
                    or KernelActionService.narrative_for_outcome(origin, outcome)
                ),
            )
            next_adjudication = None
            plan = batch.get("kernel_plan") if batch is not None else None
            if plan is not None and plan["status"] == "active":
                prepared = kernel_actions.prepare_next_plan_step(
                    str(plan["id"]), identity
                )
                if prepared is not None:
                    next_action, next_proposal, _ = prepared
                    next_adjudication = ActionAdjudicationService(self.repo).create(
                        next_action,
                        next_proposal,
                        source_model="kernel:plan",
                        enforce_precheck=False,
                    )
            return AutoTurnResult(
                status=(
                    "awaiting_confirmation"
                    if next_adjudication is not None
                    else "completed"
                ),
                player_action=self.repo.get_player_action(str(action_id)),
                proposal=approved,
                checks=checks,
                adjudication=next_adjudication,
                message=(
                    "计划当前步骤已结算；下一步骤等待确认。"
                    if next_adjudication is not None
                    else "检定后果已由规则内核原子结算。"
                ),
            )
        proposal: dict | None = None
        last_error: str | None = None
        for attempt in range(3):
            try:
                proposal = await CheckConsequenceService(self.repo).generate(
                    GenerateCheckConsequenceCommand(check_id=check_id),
                    identity,
                    director,
                    source_model=source_model,
                )
                break
            except RECOVERABLE_AUTO_TURN_ERRORS as exc:
                last_error = str(exc)
                if attempt < 2:
                    continue
        if proposal is None:
            proposal = self._fallback_consequence(
                action,
                identity,
                checks=checks,
                reason=last_error or "unknown",
            )
        if automation_level == "ai_kp":
            proposal = self.repo.clear_draft_world_effects(str(proposal["id"]))
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

    def _semantic_profile(self) -> str:
        configuration = self.repo.get_model_configuration()
        return (
            "large"
            if configuration and configuration.get("semantic_profile") == "large"
            else "small"
        )

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

    def _fallback_action_result(
        self,
        action: dict,
        identity: AuthenticatedMember,
        *,
        reason: str,
    ) -> AutoTurnResult:
        """Preserve the player review boundary when an optional model route fails."""

        proposal = self._fallback_proposal(action, identity, reason=reason)
        adjudication = ActionAdjudicationService(self.repo).create(
            action,
            proposal,
            source_model="auto-kp-fallback",
            source_error=reason,
        )
        return AutoTurnResult(
            status="awaiting_confirmation",
            player_action=self.repo.get_player_action(str(action["id"])),
            proposal=self.repo.get_turn_proposal(str(proposal["id"])),
            adjudication=adjudication,
            message="AI KP 暂时无法可靠裁定；请补充行动后重试。",
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
            pushed_failure = self._pushed_failure_narration(visible)
            if pushed_failure is not None:
                narration = pushed_failure
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

    @staticmethod
    def _pushed_failure_narration(checks: list[dict]) -> str | None:
        """Preserve the player's declared pushed approach and accepted stakes."""

        parents = {str(item.get("pushed_from_check_id")): item for item in checks}
        failed = next(
            (
                item
                for item in parents.values()
                if item.get("passed") is False
                and item.get("pushed_from_check_id")
            ),
            None,
        )
        if failed is None:
            return None
        parent_id = str(failed["pushed_from_check_id"])
        parent = next(
            (item for item in checks if str(item.get("id")) == parent_id), None
        )
        pushed_action = next(
            (
                item
                for item in (parent or {}).get("actions", ())
                if item.get("action_type") == "pushed" and str(item.get("reason", "")).strip()
            ),
            None,
        )
        declared = str((pushed_action or {}).get("reason", "")).strip()
        if declared:
            return (
                "孤注一掷失败。你改变做法并明确接受了风险："
                f"{declared} 未能达成目标，所接受的严重风险现在发生。"
            )
        return "孤注一掷失败；行动没有达成目标，并触发了比普通失败更严重的后果。"


__all__ = ["AutoTurnResult", "AutoTurnService"]
