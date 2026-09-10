"""Player-owned, restart-safe encounter action consent workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_kp.application.errors import ConflictError, InvalidInputError
from ai_kp.application.gameplay_service import GameplayCommand, GameplayService
from ai_kp.application.ports.director import EncounterIntentDirector
from ai_kp.application.session_continuity_service import SessionContinuityService
from ai_kp.director.encounter_intent import PROMPT_VERSION, EncounterIntentOutput
from ai_kp.platform.sessions.models import AuthenticatedMember
from ai_kp.rulesets.coc7.encounter_actions import Coc7EncounterTurnAgent


@dataclass(frozen=True)
class EncounterActionPreviewCommand:
    client_action_id: str
    expected_encounter_version: int
    action_text: str
    action_key: str
    target_id: str | None = None


class BoundedEncounterIntentAgent:
    """Build a consent preview from an explicit ruleset catalogue selection.

    A model-backed agent can suggest the selection, but this boundary never
    infers executable effects from keywords in the player's prose.
    """

    def preview(
        self,
        *,
        option: dict[str, Any],
        action_text: str,
        actor_name: str,
        target_name: str | None,
    ) -> dict[str, Any]:
        return {
            "action_key": option["action_key"],
            "action_label": option["label"],
            "action_kind": option["kind"],
            "action_text": action_text,
            "actor_name": actor_name,
            "target_name": target_name,
            "target_required": option["target_required"],
            "description": option["description"],
            "confirmation_required": option["kind"] != "improvised",
            "public_message": (
                "这是规则外行动；遭遇意图 Agent 需先追问或提出可编辑方案。"
                if option["kind"] == "improvised"
                else "请确认这个动作、目标和公开做法；骰值尚未生成。"
            ),
        }


class EncounterActionService:
    def __init__(
        self,
        repo: Any,
        *,
        turn_agent: Coc7EncounterTurnAgent | None = None,
        intent_agent: BoundedEncounterIntentAgent | None = None,
    ):
        self.repo = repo
        self.turn_agent = turn_agent or Coc7EncounterTurnAgent()
        self.intent_agent = intent_agent or BoundedEncounterIntentAgent()

    def options(self, encounter_id: str, identity: AuthenticatedMember) -> dict[str, Any]:
        encounter = self._encounter(encounter_id, identity)
        participant = self._owned_active_participant(encounter, identity)
        targets = [
            {
                "participant_id": str(item["participant_id"]),
                "name": str(item["name"]),
            }
            for item in encounter["state"].get("participants") or []
            if item["participant_id"] != participant["participant_id"]
            and self._can_be_targeted(item)
        ]
        return {
            "encounter_id": encounter_id,
            "encounter_version": encounter["version"],
            "participant_id": participant["participant_id"],
            "participant_name": participant["name"],
            "options": [
                option.as_dict() for option in self.turn_agent.options(encounter, participant)
            ],
            "targets": targets,
            "active_request": self._safe_request(
                self.repo.get_active_encounter_action_request(
                    encounter_id, identity.member_id
                )
            ),
        }

    def create_preview(
        self,
        encounter_id: str,
        identity: AuthenticatedMember,
        command: EncounterActionPreviewCommand,
    ) -> dict[str, Any]:
        SessionContinuityService(self.repo).require_actions_allowed(identity)
        encounter = self._encounter(encounter_id, identity)
        if int(encounter["version"]) != command.expected_encounter_version:
            raise ConflictError("Encounter changed; refresh before choosing an action")
        participant = self._owned_active_participant(encounter, identity)
        option = next(
            (
                item.as_dict()
                for item in self.turn_agent.options(encounter, participant)
                if item.action_key == command.action_key
            ),
            None,
        )
        if option is None:
            raise InvalidInputError("Selected encounter action is not available")
        target = self._target(encounter, participant, command.target_id)
        if option["target_required"] and target is None:
            raise InvalidInputError("Selected encounter action requires a target")
        if not option["target_required"] and target is not None:
            raise InvalidInputError("Selected encounter action does not accept a target")
        action_text = command.action_text.strip()
        preview = self.intent_agent.preview(
            option=option,
            action_text=action_text,
            actor_name=str(participant["name"]),
            target_name=str(target["name"]) if target else None,
        )
        self.repo.begin_immediate()
        active = self.repo.get_active_encounter_action_request(
            encounter_id, identity.member_id
        )
        if active is not None:
            raise ConflictError("Change or cancel the current encounter action first")
        created = self.repo.create_encounter_action_request(
            encounter_id=encounter_id,
            campaign_id=identity.campaign_id,
            session_id=identity.session_id,
            member_id=identity.member_id,
            participant_id=str(participant["participant_id"]),
            client_action_id=command.client_action_id.strip(),
            encounter_version=int(encounter["version"]),
            action_text=action_text,
            action_key=command.action_key,
            target_id=str(target["participant_id"]) if target else None,
            status=(
                "needs_attention"
                if option["kind"] == "improvised"
                else "awaiting_confirmation"
            ),
            preview=preview,
        )
        return self._safe_request(created)

    def confirm(
        self,
        request_id: str,
        identity: AuthenticatedMember,
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        SessionContinuityService(self.repo).require_actions_allowed(identity)
        request = self._request(request_id, identity)
        if request["status"] == "committed":
            return self._safe_request(request)
        if request["status"] == "needs_attention":
            raise ConflictError("Improvised encounter actions require an agent proposal first")
        if request["status"] not in {"awaiting_confirmation", "confirmed"}:
            raise ConflictError("This encounter action can no longer be confirmed")
        encounter = self._encounter(str(request["encounter_id"]), identity)
        participant = self._owned_active_participant(encounter, identity)
        if participant["participant_id"] != request["participant_id"]:
            raise ConflictError("Encounter participant authority changed")
        if int(encounter["version"]) != int(request["encounter_version"]):
            raise ConflictError("Encounter changed; create a new action preview")
        target = self._target(encounter, participant, request.get("target_id"))

        if request["status"] == "awaiting_confirmation":
            if int(request["version"]) != expected_version:
                raise ConflictError("Encounter action changed; refresh before confirming")
            prepared = self.turn_agent.prepare(
                encounter,
                participant,
                action_key=str(request["action_key"]),
                target=target,
                proposal=dict(request["preview"]),
            ).as_dict()
            self.repo.begin_immediate()
            request = self.repo.checkpoint_encounter_action_command(
                request_id,
                expected_version=expected_version,
                prepared_command=prepared,
            )
            # Random evidence must survive a crash before the authoritative
            # encounter transition, so it is deliberately committed first.
            self.repo.connection.commit()
        prepared = request.get("prepared_command")
        if not isinstance(prepared, dict):
            raise ConflictError("Confirmed encounter action has no prepared command")
        transitioned = GameplayService(self.repo).command_encounter(
            str(request["encounter_id"]),
            identity,
            GameplayCommand(
                command_id=str(request["id"]),
                expected_version=int(request["encounter_version"]),
                command_type=str(prepared["command_type"]),
                payload=dict(prepared["payload"]),
                authority_request_id=request_id,
            ),
        )
        completed = self.repo.complete_encounter_action_request(
            request_id,
            expected_version=int(request["version"]),
            result=transitioned,
        )
        return self._safe_request(completed)

    async def propose_improvised(
        self,
        request_id: str,
        identity: AuthenticatedMember,
        *,
        expected_version: int,
        director: EncounterIntentDirector,
        source_model: str,
    ) -> dict[str, Any]:
        SessionContinuityService(self.repo).require_actions_allowed(identity)
        request = self._request(request_id, identity)
        if request["status"] != "needs_attention" or request["action_key"] != "improvised":
            raise ConflictError("Only a pending improvised action needs an agent proposal")
        if int(request["version"]) != expected_version:
            raise ConflictError("Encounter intent changed; refresh before asking the agent")
        encounter = self._encounter(str(request["encounter_id"]), identity)
        participant = self._owned_active_participant(encounter, identity)
        if int(encounter["version"]) != int(request["encounter_version"]):
            raise ConflictError("Encounter changed; create a new action preview")
        snapshot = self._intent_snapshot(encounter, participant)
        output = await director.propose_encounter_action(
            campaign_id=identity.campaign_id,
            snapshot=snapshot,
            player_action=str(request["action_text"]),
        )
        action_key, target_id, status, preview = self._validate_agent_proposal(
            encounter=encounter,
            participant=participant,
            output=output,
            action_text=str(request["action_text"]),
            source_model=source_model,
        )
        self.repo.begin_immediate()
        current = self.repo.get_encounter_action_request(request_id)
        if current["status"] != "needs_attention" or int(current["version"]) != expected_version:
            raise ConflictError("Encounter intent changed while the agent was planning")
        return self._safe_request(
            self.repo.apply_encounter_action_agent_proposal(
                request_id,
                expected_version=expected_version,
                action_key=action_key,
                target_id=target_id,
                status=status,
                preview=preview,
            )
        )

    def record_agent_clarification(
        self,
        request_id: str,
        identity: AuthenticatedMember,
        *,
        expected_version: int,
        public_message: str,
        source_model: str,
    ) -> dict[str, Any]:
        request = self._request(request_id, identity)
        if request["status"] != "needs_attention" or int(request["version"]) != expected_version:
            raise ConflictError("Encounter intent changed while the agent was planning")
        preview = dict(request["preview"])
        preview.update(
            {
                "public_message": public_message,
                "confirmation_required": False,
                "agent": {
                    "prompt_version": PROMPT_VERSION,
                    "source_model": source_model,
                    "resolution": "clarify",
                    "fallback": True,
                },
            }
        )
        self.repo.begin_immediate()
        return self._safe_request(
            self.repo.apply_encounter_action_agent_proposal(
                request_id,
                expected_version=expected_version,
                action_key="improvised",
                target_id=None,
                status="needs_attention",
                preview=preview,
            )
        )

    def cancel(
        self,
        request_id: str,
        identity: AuthenticatedMember,
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        request = self._request(request_id, identity)
        if request["status"] == "cancelled":
            return self._safe_request(request)
        self.repo.begin_immediate()
        return self._safe_request(
            self.repo.cancel_encounter_action_request(
                request_id, expected_version=expected_version
            )
        )

    def _encounter(
        self, encounter_id: str, identity: AuthenticatedMember
    ) -> dict[str, Any]:
        if identity.role != "player":
            raise PermissionError("Player encounter action access required")
        encounter = self.repo.get_coc7_encounter(encounter_id)
        if (
            encounter["campaign_id"] != identity.campaign_id
            or encounter["session_id"] != identity.session_id
        ):
            raise KeyError(f"CoC7 encounter not found: {encounter_id}")
        if encounter["status"] != "active":
            raise ConflictError("Only an active encounter accepts player actions")
        return encounter

    def _owned_active_participant(
        self, encounter: dict[str, Any], identity: AuthenticatedMember
    ) -> dict[str, Any]:
        active_id = encounter["state"]["turn_order"][int(encounter["turn_index"])]
        participant = next(
            item
            for item in encounter["state"]["participants"]
            if item["participant_id"] == active_id
        )
        investigator_id = participant.get("investigator_id")
        if not investigator_id:
            raise ConflictError("The active encounter participant is not player-controlled")
        record = self.repo.get_campaign_investigator(
            identity.campaign_id, str(investigator_id)
        )
        if identity.pc_id != record.get("legacy_pc_id"):
            raise ConflictError("It is another participant's encounter turn")
        return participant

    @staticmethod
    def _target(
        encounter: dict[str, Any],
        participant: dict[str, Any],
        target_id: str | None,
    ) -> dict[str, Any] | None:
        if target_id is None:
            return None
        target = next(
            (
                item
                for item in encounter["state"]["participants"]
                if item["participant_id"] == target_id
                and item["participant_id"] != participant["participant_id"]
            ),
            None,
        )
        if target is None or not EncounterActionService._can_be_targeted(target):
            raise InvalidInputError("Encounter target is not available")
        return target

    @staticmethod
    def _can_be_targeted(participant: dict[str, Any]) -> bool:
        return not any(
            item.get("active", True) and item.get("type") == "dead"
            for item in participant.get("conditions") or []
        )

    def _request(self, request_id: str, identity: AuthenticatedMember) -> dict[str, Any]:
        request = self.repo.get_encounter_action_request(request_id)
        if (
            request["campaign_id"] != identity.campaign_id
            or request["session_id"] != identity.session_id
            or request["member_id"] != identity.member_id
        ):
            raise KeyError(f"Encounter action request not found: {request_id}")
        return request

    def _intent_snapshot(
        self, encounter: dict[str, Any], participant: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "kind": encounter["kind"],
            "actor": {
                "id": participant["participant_id"],
                "name": participant["name"],
            },
            "allowed_actions": [
                {
                    "action_key": option.action_key,
                    "label": option.label,
                    "kind": option.kind,
                    "target_required": option.target_required,
                }
                for option in self.turn_agent.options(encounter, participant)
                if option.kind != "improvised"
            ],
            "maneuver_available": bool(
                encounter["kind"] == "combat"
                and any(
                    item.get("kind") == "melee"
                    for item in participant.get("action_profiles") or []
                )
            ),
            "targets": [
                {"id": item["participant_id"], "name": item["name"]}
                for item in encounter["state"].get("participants") or []
                if item["participant_id"] != participant["participant_id"]
                and self._can_be_targeted(item)
            ],
        }

    def _validate_agent_proposal(
        self,
        *,
        encounter: dict[str, Any],
        participant: dict[str, Any],
        output: EncounterIntentOutput,
        action_text: str,
        source_model: str,
    ) -> tuple[str, str | None, str, dict[str, Any]]:
        targets = {
            str(item["participant_id"]): item
            for item in encounter["state"].get("participants") or []
            if item["participant_id"] != participant["participant_id"]
            and self._can_be_targeted(item)
        }
        options = {
            item.action_key: item
            for item in self.turn_agent.options(encounter, participant)
            if item.kind != "improvised"
        }
        action_key = "improvised"
        target_id: str | None = None
        status = "needs_attention"
        action_label = "规则外 / Rule of Cool"
        maneuver_effect: str | None = None
        if output.resolution == "select":
            option = options.get(str(output.action_key))
            target = targets.get(str(output.target_id)) if output.target_id else None
            if option is not None and (
                (option.target_required and target is not None)
                or (not option.target_required and output.target_id is None)
            ):
                action_key = option.action_key
                target_id = str(target["participant_id"]) if target else None
                status = "awaiting_confirmation"
                action_label = option.label
        elif output.resolution == "maneuver" and encounter["kind"] == "combat":
            profile = next(
                (
                    item
                    for item in participant.get("action_profiles") or []
                    if item.get("kind") == "melee"
                ),
                None,
            )
            target = targets.get(str(output.target_id)) if output.target_id else None
            if (
                profile is not None
                and target is not None
                and int(target.get("build") or 0) - int(participant.get("build") or 0) < 3
            ):
                action_key = f"maneuver:{profile['action_key']}"
                target_id = str(target["participant_id"])
                status = "awaiting_confirmation"
                action_label = "格斗动作"
                maneuver_effect = str(output.maneuver_effect)
        public_message = output.public_message
        if status == "needs_attention" and output.resolution in {"select", "maneuver"}:
            public_message = "Agent 提案与当前规则权威不匹配。请补充具体目标或改用已列出的动作。"
        preview = {
            "action_key": action_key,
            "action_label": action_label,
            "action_kind": "maneuver" if maneuver_effect else (
                options[action_key].kind if action_key in options else "improvised"
            ),
            "action_text": action_text,
            "actor_name": participant["name"],
            "target_name": targets[target_id]["name"] if target_id else None,
            "target_required": target_id is not None,
            "description": output.reason,
            "confirmation_required": status == "awaiting_confirmation",
            "public_message": public_message,
            "maneuver_effect": maneuver_effect,
            "agent": {
                "prompt_version": PROMPT_VERSION,
                "source_model": source_model,
                "resolution": output.resolution,
            },
        }
        return action_key, target_id, status, preview

    @staticmethod
    def _safe_request(request: dict[str, Any] | None) -> dict[str, Any] | None:
        if request is None:
            return None
        return {
            key: request[key]
            for key in (
                "id",
                "encounter_id",
                "participant_id",
                "client_action_id",
                "encounter_version",
                "action_text",
                "action_key",
                "target_id",
                "status",
                "preview",
                "result",
                "version",
                "created_at",
                "updated_at",
                "committed_at",
            )
        }


__all__ = [
    "BoundedEncounterIntentAgent",
    "EncounterActionPreviewCommand",
    "EncounterActionService",
]
