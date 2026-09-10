"""Member-scoped, player-safe views of durable parallel action batches.

The durable batch is an authority object.  It deliberately contains every
participant's action, kernel preview, exact result, and audit history, so it
must never be serialized directly to a player.  This module is the single
application boundary that reduces that object to one participant's view.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ai_kp.application.agent_trace_projection import project_player_tabletop_turn
from ai_kp.platform.resolution import has_pending_push_decision
from ai_kp.platform.sessions.models import AuthenticatedMember

ParallelPlayerSelfPhase = Literal[
    "awaiting_confirmation",
    "waiting_for_others",
    "awaiting_check",
    "awaiting_push_decision",
    "waiting_for_checks",
    "ready",
    "settling",
    "settled",
    "needs_attention",
    "superseded",
]


@dataclass(frozen=True)
class ParallelActionOwnItemProjection:
    """The only batch item a player is allowed to receive."""

    action_id: str
    adjudication: Mapping[str, Any]
    checks: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "adjudication": dict(self.adjudication),
            "checks": [dict(check) for check in self.checks],
        }


@dataclass(frozen=True)
class ParallelActionPlayerProjection:
    """Stable public contract for one participating player."""

    id: str
    status: str
    version: int
    participant_count: int
    confirmed_count: int
    waiting_count: int
    self_phase: ParallelPlayerSelfPhase
    own_item: ParallelActionOwnItemProjection
    updated_at: str
    settled_at: str | None
    public_message: str

    def as_dict(self) -> dict[str, Any]:
        """Serialize only the documented player whitelist."""

        return {
            "id": self.id,
            "status": self.status,
            "version": self.version,
            "participant_count": self.participant_count,
            "confirmed_count": self.confirmed_count,
            "waiting_count": self.waiting_count,
            "self_phase": self.self_phase,
            "own_item": self.own_item.as_dict(),
            "updated_at": self.updated_at,
            "settled_at": self.settled_at,
            "public_message": self.public_message,
        }


class ParallelActionPlayerProjectionService:
    """Project one batch without disclosing another participant's authority.

    Parallel batches contain at most twelve items, so resolving ownership with
    one bounded pass is simpler than adding a second persistence projection.
    All nested output is rebuilt from explicit allowlists; copying or deleting
    a handful of known-secret fields would be unsafe when internal models grow.
    """

    def __init__(self, repo: Any):
        self.repo = repo

    def get(
        self,
        batch_id: str,
        identity: AuthenticatedMember,
    ) -> ParallelActionPlayerProjection:
        if identity.role != "player":
            raise PermissionError("Parallel player projection requires a player")

        batch = self.repo.get_parallel_action_batch(batch_id)
        if (
            batch.get("campaign_id") != identity.campaign_id
            or batch.get("session_id") != identity.session_id
        ):
            raise PermissionError("Parallel batch belongs to another session")
        self._require_active_member(identity, batch)

        owned = self._find_owned_item(batch, identity)
        adjudication = self.repo.get_action_adjudication(
            str(owned["action_id"])
        )
        if (
            adjudication.get("id") != owned.get("adjudication_id")
            or adjudication.get("action_id") != owned.get("action_id")
            or adjudication.get("proposal_id") != owned.get("proposal_id")
        ):
            raise ValueError("Parallel batch adjudication authority is inconsistent")

        all_checks = tuple(
            check
            for check in owned.get("checks") or ()
            if isinstance(check, dict)
        )
        visible_checks = tuple(
            check
            for check in all_checks
            if self._check_is_visible(check, identity)
        )
        visible_check_ids = {str(check.get("id") or "") for check in visible_checks}
        check_projections = tuple(
            self._project_check(check, visible_check_ids=visible_check_ids)
            for check in visible_checks
        )

        items = tuple(
            item for item in batch.get("items") or () if isinstance(item, dict)
        )
        confirmed_count = sum(
            1
            for item in items
            if isinstance(item.get("adjudication"), dict)
            and item["adjudication"].get("status") == "confirmed"
        )
        participant_count = len(items)
        self_phase = self._self_phase(
            batch_status=str(batch.get("status") or "needs_attention"),
            adjudication=adjudication,
            visible_checks=visible_checks,
            member_id=identity.member_id,
        )
        return ParallelActionPlayerProjection(
            id=str(batch["id"]),
            status=str(batch["status"]),
            version=int(batch["version"]),
            participant_count=participant_count,
            confirmed_count=confirmed_count,
            # This count is deliberately limited to the consent barrier.  It
            # never reveals which other member is waiting or what they chose.
            waiting_count=participant_count - confirmed_count,
            self_phase=self_phase,
            own_item=ParallelActionOwnItemProjection(
                action_id=str(owned["action_id"]),
                adjudication=self._project_adjudication(adjudication),
                checks=check_projections,
            ),
            updated_at=str(batch.get("updated_at") or ""),
            settled_at=(
                str(batch["settled_at"])
                if batch.get("settled_at") is not None
                else None
            ),
            public_message=self._public_message(self_phase),
        )

    def current(
        self,
        identity: AuthenticatedMember,
    ) -> ParallelActionPlayerProjection | None:
        """Return this player's sole active batch without exposing lookup internals."""

        if identity.role != "player":
            raise PermissionError("Parallel player projection requires a player")
        batch = self.repo.get_active_parallel_action_batch_for_member(
            identity.campaign_id,
            identity.session_id,
            identity.member_id,
        )
        return self.get(str(batch["id"]), identity) if batch is not None else None

    def _require_active_member(
        self,
        identity: AuthenticatedMember,
        batch: Mapping[str, Any],
    ) -> None:
        try:
            member = self.repo.get_session_member(identity.member_id)
        except KeyError as exc:
            raise PermissionError(
                "Parallel batch requires an active player participant"
            ) from exc
        if (
            member.get("campaign_id") != batch.get("campaign_id")
            or member.get("session_id") != batch.get("session_id")
            or member.get("role") != "player"
            or member.get("revoked_at") is not None
        ):
            raise PermissionError(
                "Parallel batch requires an active player participant"
            )

    def _find_owned_item(
        self,
        batch: Mapping[str, Any],
        identity: AuthenticatedMember,
    ) -> dict[str, Any]:
        owned: list[dict[str, Any]] = []
        for item in batch.get("items") or ():
            if not isinstance(item, dict):
                raise TypeError("Parallel batch contains a malformed item")
            try:
                action = self.repo.get_player_action(str(item["action_id"]))
            except (KeyError, TypeError) as exc:
                raise ValueError("Parallel batch ownership authority is inconsistent") from exc
            if (
                action.get("campaign_id") != batch.get("campaign_id")
                or action.get("session_id") != batch.get("session_id")
                or action.get("proposal_id") != item.get("proposal_id")
            ):
                raise ValueError("Parallel batch ownership authority is inconsistent")
            if action.get("member_id") == identity.member_id:
                owned.append(item)
        if len(owned) != 1:
            raise PermissionError(
                "Only a participating action owner can view this parallel batch"
            )
        return owned[0]

    @staticmethod
    def _check_is_visible(
        check: Mapping[str, Any],
        identity: AuthenticatedMember,
    ) -> bool:
        if (
            check.get("campaign_id") != identity.campaign_id
            or check.get("session_id") != identity.session_id
        ):
            raise ValueError("Parallel check authority is inconsistent")
        visibility = str(
            check.get("visibility")
            or ("blind" if check.get("hidden") else "public")
        )
        if visibility == "public":
            return True
        if visibility == "private":
            return check.get("roller_member_id") == identity.member_id
        if visibility == "blind":
            return False
        raise ValueError("Parallel check has unsupported visibility")

    @classmethod
    def _project_adjudication(
        cls,
        adjudication: Mapping[str, Any],
    ) -> dict[str, Any]:
        ruling = adjudication.get("ruling")
        safe_ruling = cls._allow_fields(
            ruling if isinstance(ruling, dict) else {},
            (
                "goal",
                "method",
                "target",
                "feasibility",
                "resolution",
                "maximum_effect",
                "alternative",
            ),
        )
        options = []
        for option in adjudication.get("skill_options") or ():
            if not isinstance(option, dict):
                continue
            # ``hidden`` is intentionally not projected: a blind check remains
            # blind before and after its request is materialized.
            options.append(
                cls._allow_fields(
                    option,
                    (
                        "skill_name",
                        "skill_key",
                        "target",
                        "difficulty",
                        "reason",
                        "bonus_dice",
                        "allow_push",
                        "scope",
                        "supporting_factors",
                        "automatic_information",
                        "failure_stakes",
                        "pushed_failure_stakes",
                    ),
                )
            )
        selected_skill = adjudication.get("selected_skill")
        selected_option = next(
            (
                option
                for option in options
                if selected_skill
                in {option.get("skill_key"), option.get("skill_name")}
            ),
            None,
        )
        # The durable parallel workflow binds the canonical ruleset key, while
        # the long-standing player adjudication contract exposes the display
        # name.  Project the canonical authority back to that stable UI value;
        # the confirm endpoint accepts either representation and rebinds it to
        # the exact option key under the batch lock.
        projected_selected_skill = (
            selected_option.get("skill_name")
            if selected_option is not None
            else selected_skill
        )
        projected = {
            "id": adjudication.get("id"),
            "action_id": adjudication.get("action_id"),
            "mode": adjudication.get("mode"),
            "status": adjudication.get("status"),
            "version": adjudication.get("version"),
            "reason": adjudication.get("reason"),
            "prompt": adjudication.get("prompt"),
            "skill_options": options,
            "selected_skill": projected_selected_skill,
            "source_model": adjudication.get("source_model"),
            "updated_at": adjudication.get("updated_at"),
            "confirmed_at": adjudication.get("confirmed_at"),
            "ruling": safe_ruling,
        }
        tabletop_turn = project_player_tabletop_turn(
            adjudication.get("tabletop_turn")
        )
        if tabletop_turn is not None:
            projected["tabletop_turn"] = tabletop_turn
        return projected

    @classmethod
    def _project_check(
        cls,
        check: Mapping[str, Any],
        *,
        visible_check_ids: set[str],
    ) -> dict[str, Any]:
        raw_dice = check.get("raw_dice")
        check_plan = check.get("check_plan")
        push_decision = check.get("push_decision")
        pushed_from = check.get("pushed_from_check_id")
        return {
            "id": check.get("id"),
            "skill_key": check.get("skill_key"),
            "skill_name": check.get("skill_name"),
            "target": check.get("target"),
            "difficulty": check.get("difficulty"),
            "bonus_dice": check.get("bonus_dice"),
            "visibility": check.get("visibility"),
            "allow_push": check.get("allow_push"),
            "pushed_from_check_id": (
                pushed_from if str(pushed_from or "") in visible_check_ids else None
            ),
            "status": check.get("status"),
            "input_method": check.get("input_method"),
            "raw_dice": (
                cls._allow_fields(
                    raw_dice,
                    ("ones_digit", "tens_digits", "candidates"),
                )
                if isinstance(raw_dice, dict)
                else None
            ),
            "selected_roll": check.get("selected_roll"),
            "threshold": check.get("threshold"),
            "success_level": check.get("success_level"),
            "passed": check.get("passed"),
            "check_plan": cls._allow_fields(
                check_plan if isinstance(check_plan, dict) else {},
                (
                    "scope",
                    "supporting_factors",
                    "automatic_information",
                    "failure_stakes",
                    "pushed_failure_stakes",
                ),
            ),
            "push_decision": (
                cls._allow_fields(push_decision, ("decision", "reason", "created_at"))
                if isinstance(push_decision, dict)
                else None
            ),
            "created_at": check.get("created_at"),
            "resolved_at": check.get("resolved_at"),
        }

    @staticmethod
    def _allow_fields(
        value: Mapping[str, Any],
        fields: tuple[str, ...],
    ) -> dict[str, Any]:
        return {field: value[field] for field in fields if field in value}

    @staticmethod
    def _self_phase(
        *,
        batch_status: str,
        adjudication: Mapping[str, Any],
        visible_checks: tuple[dict[str, Any], ...],
        member_id: str,
    ) -> ParallelPlayerSelfPhase:
        if batch_status == "superseded" or adjudication.get("status") == "superseded":
            return "superseded"
        if batch_status == "needs_attention":
            return "needs_attention"
        if batch_status == "settled":
            return "settled"
        if batch_status == "committing":
            return "settling"
        if batch_status == "ready":
            return "ready"
        if adjudication.get("status") == "pending":
            return "awaiting_confirmation"
        if batch_status == "awaiting_confirmation":
            return "waiting_for_others"
        if batch_status == "awaiting_checks":
            player_checks = tuple(
                check
                for check in visible_checks
                if check.get("roller_member_id") == member_id
            )
            if any(check.get("status") == "requested" for check in player_checks):
                return "awaiting_check"
            if has_pending_push_decision(player_checks):
                return "awaiting_push_decision"
            return "waiting_for_checks"
        # Unknown future internal states fail closed without exposing details.
        return "needs_attention"

    @staticmethod
    def _public_message(phase: ParallelPlayerSelfPhase) -> str:
        messages: dict[ParallelPlayerSelfPhase, str] = {
            "awaiting_confirmation": (
                "请确认你的行动裁定；你始终可以修改行动或技能选择。"
            ),
            "waiting_for_others": "你的裁定已确认，正在等待其他参与者。",
            "awaiting_check": "请完成你当前可见且由你负责的检定。",
            "awaiting_push_decision": (
                "检定未通过；请选择接受失败，或说明新的推动方式。"
            ),
            "waiting_for_checks": "你的操作已经完成，正在等待本轮其余检定。",
            "ready": "所有参与者的决定已齐备，等待统一结算。",
            "settling": "本轮正在进行统一结算，请稍候。",
            "settled": "本轮多人行动已经统一结算。",
            "needs_attention": (
                "本轮需要重新确认或由 KP 处理；不会执行未经确认的结果。"
            ),
            "superseded": "这轮行动已被新的决定取代，请提交新的行动。",
        }
        return messages[phase]


__all__ = [
    "ParallelActionOwnItemProjection",
    "ParallelActionPlayerProjection",
    "ParallelActionPlayerProjectionService",
    "ParallelPlayerSelfPhase",
]
