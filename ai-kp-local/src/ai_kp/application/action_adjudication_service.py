"""Build and apply player-owned confirmation boundaries for AI rulings."""

import re
from typing import Any

from ai_kp.application.auto_world_expansion_service import AutoWorldExpansionService
from ai_kp.application.kernel_action_service import KernelActionService
from ai_kp.application.resolution_shadow_service import LegacyResolutionShadowService
from ai_kp.application.turn_service import TurnService
from ai_kp.platform.resolution.check_catalog import check_term_occurs_in_text
from ai_kp.platform.sessions.models import AuthenticatedMember
from ai_kp.rulesets import get_campaign_ruleset


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
        checks = list(proposal.get("proposed_checks") or [])
        resolution = str(ruling.get("resolution") or "no_roll")
        explicit_skill = self._explicit_action_skill(action)
        if enforce_precheck and resolution == "check" and explicit_skill is not None:
            canonical_skill, _target = explicit_skill
            proposed_names = {
                str(item.get("skill") or "").strip().casefold() for item in checks
            }
            if canonical_skill.casefold() not in proposed_names:
                difficulty = str(
                    (checks[0].get("difficulty") if checks else None) or "regular"
                )
                checks = [{
                    "skill": canonical_skill,
                    "difficulty": difficulty,
                    "reason": f"玩家在行动中明确选择使用{canonical_skill}。",
                    "pc_id": action.get("pc_id"),
                    "hidden": False,
                }]
                ruling["method"] = canonical_skill
                ruling["reason"] = f"玩家明确选择以{canonical_skill}检验其行动。"
                proposal = self.repo.replace_draft_with_precheck(
                    str(proposal["id"]),
                    checks,
                    public_narration=str(proposal.get("public_narration") or ""),
                    action_ruling=ruling,
                )
        if enforce_precheck and not checks and resolution == "automatic":
            # A model can occasionally name a real sheet skill while also marking the
            # result automatic.  The explicit rules primitive is the safer authority:
            # using a sheet skill to obtain a substantive result requires a roll.
            method = str(ruling.get("method") or "").strip()
            resolved_method = (
                self._resolve_sheet_skill(action, method, allow_concepts=False)
                if method
                else None
            )
            if resolved_method is not None:
                canonical_method, _ = resolved_method
                checks = [{
                    "skill": canonical_method,
                    "difficulty": "regular",
                    "reason": f"行动明确使用{canonical_method}取得结果。",
                    "pc_id": action.get("pc_id"),
                    "hidden": False,
                }]
                ruling["resolution"] = "check"
                proposal = self.repo.replace_draft_with_precheck(
                    str(proposal["id"]),
                    checks,
                    public_narration=str(proposal.get("public_narration") or ""),
                    action_ruling=ruling,
                )
                resolution = "check"
        options = self._valid_skill_options(action, checks)
        if source_error or ruling.get("feasibility") == "impossible":
            mode = "roleplay_or_clarification"
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
                    "bonus_dice": selected_option["bonus_dice"],
                    "allow_push": selected_option["allow_push"],
                    "scope": selected_option["scope"],
                    "supporting_factors": selected_option["supporting_factors"],
                    "automatic_information": selected_option[
                        "automatic_information"
                    ],
                    "failure_stakes": selected_option["failure_stakes"],
                    "pushed_failure_stakes": selected_option[
                        "pushed_failure_stakes"
                    ],
                }],
            )

        reason = str(ruling.get("reason") or "AI 未能给出可验证的裁定理由。")
        prompt = ""
        if source_error:
            prompt = "AI 输出未通过结构校验。请补充目标、手段或对话内容后重新裁定。"
        elif mode == "roleplay_or_clarification":
            prompt = str(ruling.get("alternative") or "请补充具体手段或角色扮演内容。")
        selected = options[0]["skill_name"] if options else None
        adjudication = self.repo.create_action_adjudication(
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
        LegacyResolutionShadowService(self.repo).record(action, proposal, adjudication)
        return adjudication

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
        kernel_payload = KernelActionService.kernel_payload(pending_proposal)
        override_public_narration = None
        if kernel_payload is not None and adjudication["mode"] == "direct_resolution":
            kernel_actions = KernelActionService(self.repo)
            batch = kernel_actions.commit(
                pending_proposal, outcome="success", identity=kp_identity
            )
            override_public_narration = KernelActionService.narrative_for_outcome(
                pending_proposal, "success"
            )
        proposal = self.turns.approve(
            str(adjudication["proposal_id"]),
            str(action["campaign_id"]),
            kp_identity,
            note=f"player {identity.member_id} confirmed AI ruling v{expected_version}",
            override_public_narration=override_public_narration,
        )
        if kernel_payload is not None and adjudication["mode"] == "direct_resolution":
            plan = batch.get("kernel_plan") if batch is not None else None
            if plan is not None and plan["status"] == "active":
                prepared = kernel_actions.prepare_next_plan_step(
                    str(plan["id"]), kp_identity
                )
                if prepared is not None:
                    next_action, next_proposal, _ = prepared
                    self.create(
                        next_action,
                        next_proposal,
                        source_model="kernel:plan",
                        enforce_precheck=False,
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
            resolved = self._resolve_sheet_skill(action, skill_name)
            if resolved is None:
                continue
            canonical_name, target = resolved
            if canonical_name.casefold() in seen:
                continue
            seen.add(canonical_name.casefold())
            options.append(
                {
                    "skill_name": canonical_name,
                    "skill_key": target["skill_key"],
                    "target": target["target"],
                    "difficulty": check.get("difficulty", "regular"),
                    "reason": check.get("reason") or "AI 推荐的可验证检定。",
                    "hidden": bool(check.get("hidden")),
                    "bonus_dice": int(check.get("bonus_dice") or 0),
                    "allow_push": bool(check.get("allow_push", True)),
                    "scope": str(check.get("scope") or ""),
                    "supporting_factors": list(
                        check.get("supporting_factors") or ()
                    ),
                    "automatic_information": list(
                        check.get("automatic_information") or ()
                    ),
                    "failure_stakes": str(check.get("failure_stakes") or ""),
                    "pushed_failure_stakes": str(
                        check.get("pushed_failure_stakes") or ""
                    ),
                }
            )
        return options

    def _explicit_action_skill(
        self, action: dict
    ) -> tuple[str, dict[str, Any]] | None:
        """Return one unambiguously named sheet skill from the player's text.

        The player-owned skill choice outranks a model-inferred different skill.
        Multiple named skills remain ambiguous and are left to the normal manual
        confirmation flow instead of guessing which mention was intentional.
        """

        action_text = str(action.get("action_text") or "")
        if not action_text.strip():
            return None
        campaign_id = str(action["campaign_id"])
        pc_id = action.get("pc_id")
        campaign_row = self.repo.connection.execute(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        if campaign_row is None:
            return None
        catalog = get_campaign_ruleset(dict(campaign_row)).scenario_check_catalog()
        entries_by_key = {entry.check_key: entry for entry in catalog.entries}
        matches: list[tuple[str, dict[str, Any]]] = []
        for sheet_target in self.repo.list_character_skill_targets(campaign_id, pc_id):
            canonical_name = str(sheet_target.get("skill_name") or "").strip()
            skill_key = str(sheet_target.get("skill_key") or "").strip()
            entry = entries_by_key.get(skill_key)
            candidates = (
                canonical_name,
                *((entry.display_name, *entry.direct_aliases) if entry else ()),
            )
            if not any(
                candidate
                and check_term_occurs_in_text(candidate, action_text)
                and self._skill_is_explicitly_selected(candidate, action_text)
                for candidate in candidates
            ):
                continue
            resolved = self.repo.resolve_skill_target(
                campaign_id, pc_id, canonical_name
            )
            if resolved is not None:
                matches.append((canonical_name, resolved))
        unique = {name.casefold(): (name, target) for name, target in matches}
        return next(iter(unique.values())) if len(unique) == 1 else None

    @staticmethod
    def _skill_is_explicitly_selected(skill_name: str, action_text: str) -> bool:
        """Distinguish choosing a skill from merely mentioning its subject."""

        escaped = re.escape(skill_name)
        quoted = re.search(
            rf"[\"'“‘]\s*{escaped}\s*[\"'”’]", action_text, re.IGNORECASE
        )
        if quoted:
            return True
        for match in re.finditer(escaped, action_text, re.IGNORECASE):
            left = action_text[max(0, match.start() - 18) : match.start()]
            right = action_text[match.end() : match.end() + 12]
            if re.search(
                r"(?:使用|选(?:择|用)|改用|要用|用自己的|掷|roll|use|using|choose)\s*$",
                left,
                re.IGNORECASE,
            ) or re.match(
                r"\s*(?:技能|检定|判定|skill|check)", right, re.IGNORECASE
            ):
                return True
        return False

    def _resolve_sheet_skill(
        self, action: dict, requested_name: str, *, allow_concepts: bool = True
    ) -> tuple[str, dict[str, Any]] | None:
        """Resolve exact sheet names, then unambiguous ruleset aliases.

        Models and imported modules often use a ruleset's English display name
        while a localized character sheet stores the canonical Chinese display
        name.  Alias interpretation belongs to the installed ruleset catalog;
        broad concepts that match several skills deliberately remain unresolved
        so the player can choose instead of receiving a guessed roll.
        """

        campaign_id = str(action["campaign_id"])
        pc_id = action.get("pc_id")
        direct = self.repo.resolve_skill_target(campaign_id, pc_id, requested_name)
        if direct is not None:
            return requested_name, direct
        campaign_row = self.repo.connection.execute(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        if campaign_row is None:
            return None
        catalog = get_campaign_ruleset(dict(campaign_row)).scenario_check_catalog()
        resolved_keys = (
            catalog.resolve(requested_name)
            if allow_concepts
            else catalog.resolve_direct(requested_name)
        )
        if len(resolved_keys) != 1:
            return None
        resolved_key = resolved_keys[0]
        matching_targets = [
            item
            for item in self.repo.list_character_skill_targets(campaign_id, pc_id)
            if str(item.get("skill_key") or "") == resolved_key
        ]
        if len(matching_targets) != 1:
            return None
        canonical_name = str(matching_targets[0]["skill_name"])
        target = self.repo.resolve_skill_target(campaign_id, pc_id, canonical_name)
        return (canonical_name, target) if target is not None else None

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
