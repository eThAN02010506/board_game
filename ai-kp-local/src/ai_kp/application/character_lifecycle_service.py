"""Deterministic character death, control, departure, and return workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_kp.platform.sessions.models import AuthenticatedMember

LIFECYCLE_ACTIONS = {
    "observe",
    "replace",
    "retire",
    "temporary_leave",
    "npc_control",
    "return",
    "resurrect",
}
KP_OVERRIDE_ACTIONS = {"temporary_leave", "npc_control"}
TERMINAL_CHARACTER_STATES = {"dead", "retired"}


@dataclass(frozen=True)
class LifecycleProposalCommand:
    member_id: str
    action: str
    reason: str
    replacement_investigator_id: str | None = None


@dataclass(frozen=True)
class LifecycleDecisionCommand:
    action: str
    reason: str
    expected_version: int


class CharacterLifecycleService:
    def __init__(self, repo: Any):
        self.repo = repo

    def view(self, campaign_id: str, identity: AuthenticatedMember) -> dict[str, Any]:
        self._require_campaign(identity, campaign_id)
        requests = self.repo.list_lifecycle_requests(
            campaign_id,
            member_id=None if identity.role == "kp" else identity.member_id,
        )
        events = self.repo.list_lifecycle_events(campaign_id)
        return {
            "characters": self.repo.list_investigator_lifecycles(campaign_id),
            "presence": self._presence_projection(campaign_id, identity),
            "requests": requests,
            "events": [self._public_event(item) for item in events],
            "capabilities": {
                "resurrection": False,
                "resurrection_reason": (
                    "当前规则插件未声明确定性复活规则；可通过换角继续 Campaign。"
                ),
                "replacement_character": True,
                "observer_after_death": True,
            },
        }

    def propose(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: LifecycleProposalCommand,
    ) -> dict[str, Any]:
        self._require_kp(identity, campaign_id)
        if command.action not in LIFECYCLE_ACTIONS:
            raise ValueError("Unsupported lifecycle action")
        if command.action == "resurrect":
            raise ValueError(
                "The active ruleset does not support deterministic resurrection"
            )
        reason = command.reason.strip()
        if not reason:
            raise ValueError("Lifecycle proposal requires an auditable reason")
        member = self.repo.get_session_member(command.member_id)
        self._require_target_member(identity, member)
        current_investigator = self._investigator_for_member(member)
        replacement = command.replacement_investigator_id
        self._validate_action(
            command.action,
            member,
            current_investigator,
            replacement,
        )
        lifecycle = None
        if current_investigator is not None:
            lifecycle = self.repo.ensure_investigator_lifecycle(
                campaign_id, current_investigator
            )
        created = self.repo.create_lifecycle_request(
            campaign_id=campaign_id,
            session_id=identity.session_id,
            member_id=command.member_id,
            investigator_id=current_investigator,
            replacement_investigator_id=replacement,
            action=command.action,
            reason=reason,
            base_lifecycle_version=(lifecycle or {}).get("version"),
            proposed_by_member_id=identity.member_id,
        )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=campaign_id,
            audience="member",
            member_id=command.member_id,
            event_type="character.lifecycle_proposed",
            resource_type="character_lifecycle_request",
            resource_id=str(created["id"]),
            payload={"action": command.action},
        )
        return created

    def decide(
        self,
        request_id: str,
        identity: AuthenticatedMember,
        command: LifecycleDecisionCommand,
    ) -> dict[str, Any]:
        request = self.repo.get_lifecycle_request(request_id)
        self._require_campaign(identity, str(request["campaign_id"]))
        if command.action not in {"accept", "reject"}:
            raise ValueError("Lifecycle decision must be accept or reject")
        reason = command.reason.strip()
        if not reason:
            raise ValueError("Lifecycle decision requires an auditable reason")
        is_owner = identity.member_id == request["member_id"]
        is_kp_override = (
            identity.role == "kp" and request["action"] in KP_OVERRIDE_ACTIONS
        )
        if not is_owner and not is_kp_override:
            raise PermissionError("Only the affected player may decide this request")
        if command.action == "reject":
            return self.repo.decide_lifecycle_request(
                request_id,
                expected_version=command.expected_version,
                status="rejected",
                decided_by_member_id=identity.member_id,
            )
        self.repo.begin_immediate()
        request = self.repo.get_lifecycle_request(request_id)
        if request["version"] != command.expected_version:
            raise ValueError("Lifecycle request changed; refresh and retry")
        result = self._apply(request, identity, reason)
        decided = self.repo.decide_lifecycle_request(
            request_id,
            expected_version=command.expected_version,
            status="applied",
            decided_by_member_id=identity.member_id,
        )
        return {"request": decided, **result}

    def sync_character_state(
        self,
        *,
        campaign_id: str,
        session_id: str,
        investigator_id: str,
        character_state: dict[str, Any],
        source_event_id: str,
        actor_member_id: str,
    ) -> dict[str, Any] | None:
        lifecycle = self.repo.ensure_investigator_lifecycle(
            campaign_id, investigator_id
        )
        active_conditions = {
            str(item.get("type"))
            for item in character_state.get("conditions", [])
            if isinstance(item, dict) and item.get("active", True)
        }
        target = (
            "dead"
            if "dead" in active_conditions
            else "incapacitated"
            if active_conditions & {"dying", "unconscious"}
            else "active"
        )
        current = str(lifecycle["state"])
        if current in TERMINAL_CHARACTER_STATES or current in {
            "departed",
            "npc_controlled",
        }:
            return None
        if current == target:
            return None
        transitioned = self.repo.transition_investigator_lifecycle(
            campaign_id,
            investigator_id,
            expected_version=int(lifecycle["version"]),
            to_state=target,
            source_event_id=source_event_id,
        )
        public_summary = self._state_summary(target)
        event = self.repo.append_lifecycle_event(
            campaign_id=campaign_id,
            session_id=session_id,
            investigator_id=investigator_id,
            command_id=f"gameplay:{source_event_id}",
            action="ruleset_state_sync",
            from_state=current,
            to_state=target,
            reason="规则插件根据已提交的角色状态更新生命周期",
            source_event_id=source_event_id,
            actor_member_id=actor_member_id,
            public_summary=public_summary,
        )
        self.repo.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience="session",
            event_type="character.lifecycle_changed",
            resource_type="investigator",
            resource_id=investigator_id,
            payload={"state": target, "summary": public_summary},
        )
        return {"lifecycle": transitioned, "event": event}

    def _apply(
        self,
        request: dict[str, Any],
        identity: AuthenticatedMember,
        decision_reason: str,
    ) -> dict[str, Any]:
        campaign_id = str(request["campaign_id"])
        member_id = str(request["member_id"])
        member = self.repo.get_session_member(member_id)
        current_id = request.get("investigator_id")
        action = str(request["action"])
        replacement = request.get("replacement_investigator_id")
        presence = self.repo.ensure_member_presence(
            campaign_id=campaign_id,
            session_id=str(request["session_id"]),
            member_id=member_id,
            investigator_id=str(current_id) if current_id else None,
            state="active" if member["role"] == "player" else "observing",
        )
        from_state = None
        if current_id:
            current = self.repo.ensure_investigator_lifecycle(
                campaign_id, str(current_id)
            )
            from_state = str(current["state"])
            expected = request.get("base_lifecycle_version")
            if expected is not None and int(current["version"]) != int(expected):
                raise ValueError("Character lifecycle changed; request must be recreated")

        target_presence, target_lifecycle, target_investigator = {
            "observe": ("observing", "departed", None),
            "retire": ("observing", "retired", None),
            "temporary_leave": ("temporarily_absent", "departed", None),
            "npc_control": ("npc_controlled", "npc_controlled", None),
            "replace": ("active", None, replacement),
            "return": ("active", "active", replacement or current_id),
        }[action]

        if target_lifecycle and current_id and from_state not in TERMINAL_CHARACTER_STATES:
            current = self.repo.get_investigator_lifecycle(campaign_id, str(current_id))
            self.repo.transition_investigator_lifecycle(
                campaign_id,
                str(current_id),
                expected_version=int(current["version"]),
                to_state=target_lifecycle,
                source_event_id=str(request["id"]),
            )

        pc_id = None
        if target_investigator:
            record = self._validate_replacement(campaign_id, member, str(target_investigator))
            pc_id = str(record["legacy_pc_id"])
            replacement_lifecycle = self.repo.ensure_investigator_lifecycle(
                campaign_id, str(target_investigator)
            )
            if replacement_lifecycle["state"] in TERMINAL_CHARACTER_STATES:
                raise ValueError("A dead or retired investigator cannot be assigned")
            if replacement_lifecycle["state"] != "active":
                self.repo.transition_investigator_lifecycle(
                    campaign_id,
                    str(target_investigator),
                    expected_version=int(replacement_lifecycle["version"]),
                    to_state="active",
                    source_event_id=str(request["id"]),
                )
        self.repo.set_member_control(
            member_id,
            role="player" if target_presence == "active" else "observer",
            pc_id=pc_id,
        )
        presence = self.repo.transition_member_presence(
            campaign_id,
            member_id,
            expected_version=int(presence["version"]),
            state=target_presence,
            investigator_id=str(target_investigator) if target_investigator else None,
        )
        public_summary = self._action_summary(action)
        event = self.repo.append_lifecycle_event(
            campaign_id=campaign_id,
            session_id=str(request["session_id"]),
            investigator_id=(
                str(target_investigator)
                if target_investigator
                else str(current_id)
                if current_id
                else None
            ),
            member_id=member_id,
            command_id=f"request:{request['id']}",
            action=action,
            from_state=from_state,
            to_state=(
                from_state
                if from_state in TERMINAL_CHARACTER_STATES and target_lifecycle
                else target_lifecycle or "active"
            ),
            reason=f"{request['reason']} / {decision_reason}",
            source_event_id=str(request["id"]),
            actor_member_id=identity.member_id,
            public_summary=public_summary,
            details={"presence": target_presence},
        )
        self.repo.append_realtime_event(
            session_id=str(request["session_id"]),
            campaign_id=campaign_id,
            audience="session",
            event_type="character.lifecycle_changed",
            resource_type="character_lifecycle_request",
            resource_id=str(request["id"]),
            payload={"summary": public_summary},
        )
        return {
            "member": self.repo.get_session_member(member_id),
            "presence": presence,
            "event": event,
        }

    def _presence_projection(
        self, campaign_id: str, identity: AuthenticatedMember
    ) -> list[dict[str, Any]]:
        rows = self.repo.list_member_presence(campaign_id, identity.session_id)
        return [
            {
                "member_id": row["member_id"],
                "display_name": row["display_name"],
                "state": row["state"],
                "investigator_id": row["investigator_id"],
                "version": row["version"],
            }
            for row in rows
            if identity.role == "kp" or row["member_id"] == identity.member_id
        ]

    def _validate_action(
        self,
        action: str,
        member: dict[str, Any],
        current_investigator: str | None,
        replacement: str | None,
    ) -> None:
        if action in {"observe", "retire", "temporary_leave", "npc_control"} and (
            member["role"] != "player" or current_investigator is None
        ):
            raise ValueError("This action requires an actively controlled investigator")
        if action == "replace":
            if not replacement:
                raise ValueError("Replacement requires a replacement investigator")
            if current_investigator:
                current = self.repo.ensure_investigator_lifecycle(
                    str(member["campaign_id"]), current_investigator
                )
                if current["state"] not in {"dead", "retired", "departed"}:
                    raise ValueError("The current investigator must first leave active play")
            self._validate_replacement(str(member["campaign_id"]), member, replacement)
        if action == "return":
            target = replacement or current_investigator
            if not target or member["role"] != "observer":
                raise ValueError("Return requires an observing member and investigator")
            self._validate_replacement(str(member["campaign_id"]), member, target)

    def _validate_replacement(
        self, campaign_id: str, member: dict[str, Any], investigator_id: str
    ) -> dict[str, Any]:
        record = self.repo.get_campaign_investigator(campaign_id, investigator_id)
        if not record.get("approved_revision_id") or not record.get("legacy_pc_id"):
            raise ValueError("Only an approved investigator can enter play")
        if record["owner_profile_id"] != member.get("player_profile_id"):
            raise PermissionError("Investigator belongs to another player profile")
        occupied = self.repo.connection.execute(
            """
            SELECT id FROM session_members
            WHERE session_id = ? AND pc_id = ? AND revoked_at IS NULL AND id != ?
            """,
            (member["session_id"], record["legacy_pc_id"], member["id"]),
        ).fetchone()
        if occupied is not None:
            raise ValueError("Investigator is already controlled by another member")
        return record

    def _investigator_for_member(self, member: dict[str, Any]) -> str | None:
        pc_id = member.get("pc_id")
        if not pc_id:
            try:
                presence = self.repo.get_member_presence(
                    str(member["campaign_id"]), str(member["id"])
                )
                return (
                    str(presence["investigator_id"])
                    if presence.get("investigator_id")
                    else None
                )
            except KeyError:
                return None
        return str(
            self.repo.find_investigator_by_pc(str(member["campaign_id"]), str(pc_id))[
                "investigator_id"
            ]
        )

    @staticmethod
    def _public_event(event: dict[str, Any]) -> dict[str, Any]:
        return {
            key: event[key]
            for key in (
                "id",
                "investigator_id",
                "member_id",
                "action",
                "from_state",
                "to_state",
                "public_summary",
                "created_at",
                "sequence",
            )
        }

    @staticmethod
    def _state_summary(state: str) -> str:
        return {
            "active": "调查员恢复到可行动状态。",
            "incapacitated": "调查员已失去行动能力，但 Campaign 继续。",
            "dead": "调查员已经死亡；玩家可观战或在确认后换角继续。",
        }[state]

    @staticmethod
    def _action_summary(action: str) -> str:
        return {
            "observe": "玩家转为观战，Campaign 继续。",
            "replace": "玩家确认由另一名已审核角色加入继续游戏。",
            "retire": "调查员退出当前冒险，历史记录保留。",
            "temporary_leave": "玩家暂时离席，角色历史与席位保留。",
            "npc_control": "角色暂时交由主持方控制，变更已记录。",
            "return": "玩家回归并重新取得角色控制权。",
        }[action]

    @staticmethod
    def _require_campaign(identity: AuthenticatedMember, campaign_id: str) -> None:
        if identity.campaign_id != campaign_id:
            raise KeyError("Campaign lifecycle not found")
        if identity.role not in {"kp", "player", "observer"}:
            raise PermissionError("Campaign membership required")

    @staticmethod
    def _require_kp(identity: AuthenticatedMember, campaign_id: str) -> None:
        CharacterLifecycleService._require_campaign(identity, campaign_id)
        if identity.role != "kp":
            raise PermissionError("Only the KP may propose lifecycle changes")

    @staticmethod
    def _require_target_member(
        identity: AuthenticatedMember, member: dict[str, Any]
    ) -> None:
        if (
            member["campaign_id"] != identity.campaign_id
            or member["session_id"] != identity.session_id
            or member["revoked_at"] is not None
            or member["role"] not in {"player", "observer"}
        ):
            raise KeyError("Target campaign member not found")


__all__ = [
    "CharacterLifecycleService",
    "LifecycleDecisionCommand",
    "LifecycleProposalCommand",
]
