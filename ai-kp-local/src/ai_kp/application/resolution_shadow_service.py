"""Read-only projection of legacy rulings into the deterministic kernel boundary."""

from __future__ import annotations

from typing import Any

from ai_kp.platform.resolution.action_kernel import (
    ActionOperator,
    ResolutionPolicy,
    ScenarioContract,
    ScenarioSnapshot,
    SkillChoice,
)
from ai_kp.platform.resolution.selected_action import (
    SelectedOperator,
    prepare_selected_kernel_action,
)


class LegacyResolutionShadowService:
    """Record comparable previews without changing legacy proposal authority."""

    SOURCE = "legacy_projection"

    def __init__(self, repo: Any):
        self.repo = repo

    def record(
        self,
        action: dict[str, Any],
        proposal: dict[str, Any],
        adjudication: dict[str, Any],
    ) -> dict[str, Any] | None:
        create_preview = getattr(self.repo, "create_action_resolution_preview", None)
        if create_preview is None:
            return None
        run = self._active_run(str(action["campaign_id"]))
        run_id = str(run["id"]) if run else None
        snapshot = ScenarioSnapshot(
            run_id=run_id or f"unbound:{action['campaign_id']}",
            contract_id=f"legacy-projection:{run['module_id'] if run else action['campaign_id']}",
            scenario_version=1,
            run_version=int(run.get("version", 0)) if run else 0,
            status=str(run.get("status", "active")) if run else "active",
            scene_id=run.get("current_scene_key") if run else None,
            facts=dict(run.get("state") or {}) if run else {},
        )
        operator = self._operator(proposal, adjudication)
        selected_key = self._selected_skill_key(adjudication)
        contract = ScenarioContract(
            contract_id=snapshot.contract_id,
            source_version=snapshot.scenario_version,
            ruleset_id="legacy-shadow",
            title="Legacy ruling shadow projection",
            operators=(operator,),
        )
        prepared = prepare_selected_kernel_action(
            contract,
            snapshot,
            action_id=str(action["id"]),
            actor_id=str(action.get("pc_id") or action["member_id"]),
            player_action=str(action.get("action_text") or "Legacy player action"),
            selection=SelectedOperator(
                operator_id=operator.operator_id,
                requested_skill_key=selected_key,
            ),
        )
        return create_preview(
            action_id=str(action["id"]),
            run_id=run_id,
            source=self.SOURCE,
            preview=prepared.preview,
        )

    def _active_run(self, campaign_id: str) -> dict[str, Any] | None:
        getter = getattr(self.repo, "get_active_campaign_module_run", None)
        return getter(campaign_id) if getter is not None else None

    @staticmethod
    def _operator(
        proposal: dict[str, Any], adjudication: dict[str, Any]
    ) -> ActionOperator:
        mode = str(adjudication.get("mode") or "roleplay_or_clarification")
        options = tuple(
            SkillChoice(
                skill_key=str(option["skill_key"]),
                difficulty=LegacyResolutionShadowService._difficulty(option.get("difficulty")),
                reason=str(option.get("reason") or "Legacy authorized check option."),
                hidden=bool(option.get("hidden")),
                bonus_dice=int(option.get("bonus_dice") or 0),
                allow_push=bool(option.get("allow_push", True)),
                scope=str(option.get("scope") or ""),
                supporting_factors=tuple(option.get("supporting_factors") or ()),
                automatic_information=tuple(
                    option.get("automatic_information") or ()
                ),
                failure_stakes=str(option.get("failure_stakes") or ""),
                pushed_failure_stakes=str(
                    option.get("pushed_failure_stakes") or ""
                ),
            )
            for option in adjudication.get("skill_options") or ()
            if option.get("skill_key")
        )
        policy: ResolutionPolicy
        if mode == "direct_resolution":
            policy = "automatic"
        elif mode == "skill_check" and options:
            policy = "required_check"
        else:
            policy = "clarification"
        ruling = dict(proposal.get("action_ruling") or {})
        return ActionOperator(
            operator_id=f"legacy:{proposal['id']}",
            title="Legacy ruling projection",
            policy=policy,
            skill_choices=options,
            rationale=str(ruling.get("reason") or adjudication.get("reason") or ""),
            maximum_effect=str(ruling.get("maximum_effect") or ""),
            clarification_prompt=str(
                adjudication.get("prompt") or ruling.get("alternative") or ""
            ),
        )

    @staticmethod
    def _difficulty(value: Any) -> str:
        normalized = str(value or "regular")
        return normalized if normalized in {"regular", "hard", "extreme"} else "regular"

    @staticmethod
    def _selected_skill_key(adjudication: dict[str, Any]) -> str | None:
        selected = adjudication.get("selected_skill")
        for option in adjudication.get("skill_options") or ():
            if option.get("skill_name") == selected and option.get("skill_key"):
                return str(option["skill_key"])
        return None


__all__ = ["LegacyResolutionShadowService"]
