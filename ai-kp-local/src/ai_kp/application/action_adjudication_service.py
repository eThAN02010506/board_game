"""Build and apply player-owned confirmation boundaries for AI rulings."""

from typing import Any

from ai_kp.application.auto_world_expansion_service import AutoWorldExpansionService
from ai_kp.application.turn_service import TurnService
from ai_kp.platform.sessions.models import AuthenticatedMember


class ActionAdjudicationService:
    def __init__(self, repo: Any):
        self.repo = repo
        self.turns = TurnService(repo)

    def create(
        self,
        action: dict,
        proposal: dict,
        *,
        source_model: str,
        source_error: str | None = None,
        enforce_precheck: bool = True,
    ) -> dict:
        ruling = dict(proposal.get("action_ruling") or {})
        checks, required_precheck = (
            self._apply_required_precheck(action, proposal)
            if enforce_precheck
            else (list(proposal.get("proposed_checks") or []), False)
        )
        options = self._valid_skill_options(action, checks)
        resolution = str(ruling.get("resolution") or "no_roll")
        deterministic_clarification = self._requires_deterministic_clarification(action)
        if source_error or deterministic_clarification or ruling.get("feasibility") == "impossible":
            mode = "roleplay_or_clarification"
        elif required_precheck:
            mode = "skill_check" if options else "roleplay_or_clarification"
        elif resolution in {"check", "opposed"} and options:
            mode = "skill_check"
        elif resolution == "automatic":
            mode = "direct_resolution"
        else:
            mode = "roleplay_or_clarification"

        if mode == "skill_check":
            selected_option = options[0]
            self.repo.replace_draft_proposed_checks(
                str(proposal["id"]),
                [{
                    "skill": selected_option["skill_name"],
                    "difficulty": selected_option["difficulty"],
                    "reason": selected_option["reason"],
                    "pc_id": action.get("pc_id"),
                    "hidden": selected_option["hidden"],
                }],
            )

        reason = (
            str(options[0]["reason"])
            if required_precheck and options
            else "该行动需要前置判定，但角色卡上没有可验证的相关技能；请补充具体做法。"
            if required_precheck
            else "行动包含当前时代不存在的技术或服务，不能靠检定将其变为世界事实。"
            if deterministic_clarification
            else str(ruling.get("reason") or "AI 未能给出可验证的裁定理由。")
        )
        prompt = ""
        if source_error:
            prompt = "AI 输出未通过结构校验。请补充目标、手段或对话内容后重新裁定。"
        elif deterministic_clarification:
            prompt = "请改用符合当前时代与既有世界事实的物品、线索或行动方式。"
        elif mode == "roleplay_or_clarification":
            prompt = str(ruling.get("alternative") or "请补充具体手段或角色扮演内容。")
        selected = options[0]["skill_name"] if options else None
        return self.repo.create_action_adjudication(
            action_id=str(action["id"]),
            proposal_id=str(proposal["id"]),
            mode=mode,
            reason=reason,
            prompt=prompt,
            skill_options=options[:3],
            selected_skill=selected,
            source_model=source_model,
            source_error=source_error,
        )

    def confirm(
        self,
        action_id: str,
        *,
        expected_version: int,
        selected_skill: str | None,
        identity: AuthenticatedMember,
    ) -> tuple[dict, dict, list[dict], bool]:
        action = self._owned_action(action_id, identity)
        adjudication = self.repo.get_action_adjudication(action_id)
        if selected_skill and selected_skill != adjudication.get("selected_skill"):
            adjudication = self.repo.replace_adjudication_skill(
                action_id,
                expected_version=expected_version,
                skill_name=selected_skill,
                actor_member_id=identity.member_id,
            )
            expected_version = int(adjudication["version"])
        adjudication = self.repo.confirm_action_adjudication(
            action_id,
            expected_version=expected_version,
            actor_member_id=identity.member_id,
        )
        group = self.repo.list_proposal_adjudications(str(adjudication["proposal_id"]))
        if any(item["status"] == "pending" for item in group):
            return (
                adjudication,
                self.repo.get_turn_proposal(str(adjudication["proposal_id"])),
                [],
                False,
            )
        kp_identity = self._system_kp_identity(action)
        pending_proposal = self.repo.get_turn_proposal(str(adjudication["proposal_id"]))
        if pending_proposal.get("proposal_kind") == "world_expansion":
            world = AutoWorldExpansionService(self.repo).materialize_existing(
                pending_proposal, kp_identity
            )
            if world.status != "materialized" or world.proposal is None:
                raise ValueError(world.message)
            return adjudication, world.proposal, [], True
        proposal = self.turns.approve(
            str(adjudication["proposal_id"]),
            str(action["campaign_id"]),
            kp_identity,
            note=f"player {identity.member_id} confirmed AI ruling v{expected_version}",
        )
        return (
            adjudication,
            proposal,
            self.repo.list_skill_checks_for_action(action_id),
            True,
        )

    def revise(
        self,
        action_id: str,
        *,
        expected_version: int,
        action_text: str,
        identity: AuthenticatedMember,
    ) -> dict:
        self._owned_action(action_id, identity)
        normalized = action_text.strip()
        if not normalized:
            raise ValueError("Revised action text is required")
        old_action = self.repo.revise_player_action_for_adjudication(
            action_id,
            expected_version=expected_version,
            actor_member_id=identity.member_id,
        )
        return self.repo.create_player_action(
            identity,
            action_text=normalized,
            map_id=old_action.get("map_id"),
            client_action_id=None,
        )

    def _valid_skill_options(self, action: dict, checks: list[dict]) -> list[dict]:
        options: list[dict] = []
        seen: set[str] = set()
        for check in checks:
            skill_name = str(check.get("skill") or "").strip()
            if not skill_name or skill_name.casefold() in seen:
                continue
            target = self.repo.resolve_skill_target(
                str(action["campaign_id"]), action.get("pc_id"), skill_name
            )
            if target is None:
                continue
            seen.add(skill_name.casefold())
            options.append(
                {
                    "skill_name": skill_name,
                    "skill_key": target["skill_key"],
                    "target": target["target"],
                    "difficulty": check.get("difficulty", "regular"),
                    "reason": check.get("reason") or "AI 推荐的可验证检定。",
                    "hidden": bool(check.get("hidden")),
                }
            )
        if options:
            self._append_relevant_alternatives(action, options)
        return options

    def _append_relevant_alternatives(
        self, action: dict, options: list[dict]
    ) -> None:
        text = str(action.get("action_text") or "").casefold()
        if any(term in text for term in ("亲属", "冒充", "欺骗", "说服", "钥匙")):
            relevant = {
                "话术", "快速交谈", "说服", "魅惑", "心理学",
                "fast talk", "persuade", "charm", "psychology",
            }
        elif any(term in text for term in ("检查", "寻找", "观察", "倾听")):
            relevant = {"侦查", "聆听", "spot hidden", "listen"}
        else:
            return
        seen = {str(item["skill_name"]).casefold() for item in options}
        for candidate in self.repo.list_character_skill_targets(
            str(action["campaign_id"]), action.get("pc_id")
        ):
            name = str(candidate["skill_name"])
            if name.casefold() not in relevant or name.casefold() in seen:
                continue
            primary = options[0]
            options.append(
                {
                    **candidate,
                    "difficulty": primary["difficulty"],
                    "reason": f"可改用{name}，但必须保持同一行动目标与合理做法。",
                    "hidden": False,
                }
            )
            seen.add(name.casefold())
            if len(options) >= 3:
                break

    def _apply_required_precheck(
        self, action: dict, proposal: dict
    ) -> tuple[list[dict], bool]:
        checks = list(proposal.get("proposed_checks") or [])
        text = str(action.get("action_text") or "").casefold()
        dangerous_jump = (
            any(word in text for word in ("跳车", "jump off", "跳下列车"))
            or ("列车" in text and "跳" in text)
        )
        candidates: tuple[str, ...] = ()
        reason = ""
        narration = ""
        if dangerous_jump:
            candidates = ("Idea", "INT", "灵感", "智力")
            reason = "先确认角色是否意识到从行驶列车跳下的严重风险；成功只提供风险认知，不保证安全。"
            narration = "在真正跃出行驶列车前，先进行一次灵感/INT 风险认知检定。"
            safe_ruling = {
                "goal": "在跳离行驶列车前理解风险",
                "method": "灵感/INT 风险认知",
                "target": "即将采取危险行动的角色",
                "feasibility": "partial",
                "resolution": "check",
                "reason": reason,
                "maximum_effect": "意识到严重风险并获得重新选择机会；不执行跳车，也不保证安全。",
                "alternative": "等待列车减速、寻找制动方式，或重述跳车的具体时机与防护。",
            }
        elif any(term in text for term in ("声称", "冒充", "假装")) and any(
            term in text for term in ("亲属", "钥匙", "交出")
        ):
            candidates = ("话术", "快速交谈", "Fast Talk", "说服", "Persuade", "魅惑", "Charm")
            reason = "该说法属于欺骗或说服尝试；成功最多让目标暂时相信或让步，不能确认亲属关系为真。"
            narration = "请先选择角色卡上的社交技能，并说明具体说辞。"
            safe_ruling = {
                "goal": "说服列车长暂时相信说辞或作出让步",
                "method": "以亲属说法进行欺骗或说服",
                "target": "列车长",
                "feasibility": "partial",
                "resolution": "check",
                "reason": reason,
                "maximum_effect": "目标暂时相信或愿意进一步交涉；不确认亲属关系，也不保证交出钥匙。",
                "alternative": "输入具体台词或概述说辞，也可改用可核实的身份与请求。",
            }
        if not candidates:
            return checks, False
        for name in candidates:
            if self.repo.resolve_skill_target(
                str(action["campaign_id"]), action.get("pc_id"), name
            ) is not None:
                forced = [{
                    "skill": name,
                    "difficulty": "regular",
                    "reason": reason,
                    "pc_id": action.get("pc_id"),
                    "hidden": False,
                }]
                self.repo.replace_draft_with_precheck(
                    str(proposal["id"]),
                    forced,
                    public_narration=narration,
                    action_ruling=safe_ruling,
                )
                return forced, True
        return [], True

    @staticmethod
    def _requires_deterministic_clarification(action: dict) -> bool:
        text = str(action.get("action_text") or "").casefold()
        modern_terms = (
            "智能手机", "smartphone", "微信", "wechat", "gps", "二维码",
            "互联网", "internet", "手机定位",
        )
        return any(term in text for term in modern_terms)

    def _owned_action(self, action_id: str, identity: AuthenticatedMember) -> dict:
        action = self.repo.get_player_action(action_id)
        if identity.role != "player" or action["member_id"] != identity.member_id:
            raise ValueError("Players can only adjudicate their own actions")
        return action

    def _system_kp_identity(self, action: dict) -> AuthenticatedMember:
        row = self.repo.connection.execute(
            """
            SELECT id, session_id, campaign_id, display_name FROM session_members
            WHERE session_id = ? AND campaign_id = ? AND role = 'kp'
              AND revoked_at IS NULL ORDER BY joined_at, id LIMIT 1
            """,
            (action["session_id"], action["campaign_id"]),
        ).fetchone()
        if row is None:
            raise ValueError("No active KP member exists for automatic adjudication")
        return AuthenticatedMember(
            member_id=str(row["id"]), session_id=str(row["session_id"]),
            campaign_id=str(row["campaign_id"]), role="kp",
            display_name=f"{row['display_name']} · Auto KP", pc_id=None,
        )
