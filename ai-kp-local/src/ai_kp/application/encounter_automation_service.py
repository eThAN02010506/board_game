"""Restart-safe AI enemy and consented idle-turn encounter progression."""

from __future__ import annotations

from typing import Any

from ai_kp.application.auto_kp_queue_service import AutoKpQueueService
from ai_kp.application.gameplay_service import GameplayCommand, GameplayService
from ai_kp.application.ports.director import EnemyTurnDirector
from ai_kp.application.session_continuity_service import SessionContinuityService
from ai_kp.director.enemy_turn import PROMPT_VERSION, EnemyTurnOutput
from ai_kp.director.errors import CampaignAiCallCancelledError
from ai_kp.platform.sessions.models import AuthenticatedMember
from ai_kp.rulesets.coc7.encounter_actions import Coc7EncounterTurnAgent


class EncounterAutomationService:
    """AI selects identifiers; this service validates, checkpoints, and commits."""

    def __init__(self, repo: Any):
        self.repo = repo
        self.turn_agent = Coc7EncounterTurnAgent()

    async def advance(
        self,
        encounter_id: str,
        *,
        phase: str,
        policy: str,
        identity: AuthenticatedMember,
        director: EnemyTurnDirector,
        source_model: str,
        expected_episode_id: str | None = None,
        expected_episode_version: int | None = None,
    ) -> dict[str, Any]:
        encounter = self.repo.get_coc7_encounter(encounter_id)
        self._require_scope(encounter, identity)
        episode = self.repo.get_current_campaign_episode(identity.session_id)
        if episode is None or episode.get("status") != "in_progress":
            raise ValueError("Encounter automation requires an in-progress episode")
        frozen_episode_id = expected_episode_id or str(episode["id"])
        frozen_episode_version = (
            expected_episode_version
            if expected_episode_version is not None
            else int(episode["version"])
        )
        if (
            str(episode["id"]) != frozen_episode_id
            or int(episode["version"]) != frozen_episode_version
        ):
            raise ValueError("Encounter automation episode authority is stale")
        if self.repo.get_active_session_safety_event(identity.session_id) is not None:
            return self._safety_pause()
        participant = self._active_participant(encounter)
        self._require_phase(participant, phase)
        if phase == "idle_player" and policy == "pause":
            episode = self.repo.get_current_campaign_episode(identity.session_id)
            if episode is None:
                raise ValueError("Idle pause requires an active campaign episode")
            result = SessionContinuityService(self.repo).transition(
                identity,
                target="paused",
                expected_version=int(episode["version"]),
            )
            return {
                "status": "succeeded",
                "stage": "encounter_idle_pause",
                "message": "玩家超时；已按 Session 0 约定暂停游戏。",
                "continuity": result,
            }

        version = int(encounter["version"])
        existing = self.repo.get_encounter_automation_turn_for_version(
            encounter_id,
            version,
            str(participant["participant_id"]),
            phase,
        )
        if existing is None:
            self.repo.begin_immediate()
            existing = self.repo.create_encounter_automation_turn(
                encounter_id=encounter_id,
                campaign_id=str(encounter["campaign_id"]),
                session_id=str(encounter["session_id"]),
                encounter_version=version,
                participant_id=str(participant["participant_id"]),
                phase=phase,
                policy=policy,
            )
            self.repo.connection.commit()
        if existing["status"] == "committed":
            return {
                "status": "succeeded",
                "stage": "encounter_turn",
                "turn": existing,
            }
        if existing["status"] == "planned":
            selection = await self._selection(
                encounter,
                participant,
                phase=phase,
                policy=policy,
                director=director,
                source_model=source_model,
            )
            target = self._target(encounter, participant, selection.get("target_id"))
            prepared = self.turn_agent.prepare(
                encounter,
                participant,
                action_key=str(selection["action_key"]),
                target=target,
                proposal=None,
            ).as_dict()
            self.repo.begin_immediate()
            if self.repo.get_active_session_safety_event(identity.session_id) is not None:
                self.repo.connection.rollback()
                return self._safety_pause()
            current_encounter = self.repo.get_coc7_encounter(encounter_id)
            current_episode = self.repo.get_current_campaign_episode(identity.session_id)
            if (
                int(current_encounter["version"]) != version
                or self._active_participant(current_encounter)["participant_id"]
                != participant["participant_id"]
                or current_episode is None
                or current_episode.get("status") != "in_progress"
                or str(current_episode["id"]) != frozen_episode_id
                or int(current_episode["version"]) != frozen_episode_version
            ):
                raise ValueError("Encounter changed while the turn agent was selecting")
            existing = self.repo.checkpoint_encounter_automation_turn(
                str(existing["id"]),
                expected_version=int(existing["version"]),
                selection=selection,
                prepared_command=prepared,
            )
            # Random rolls are evidence, not retry-local state.
            self.repo.connection.commit()

        prepared = existing.get("prepared_command")
        if not isinstance(prepared, dict):
            raise TypeError("Encounter automation has no prepared rules command")
        if self.repo.get_active_session_safety_event(identity.session_id) is not None:
            return self._safety_pause()
        current_episode = self.repo.get_current_campaign_episode(identity.session_id)
        if current_episode is None or current_episode.get("status") != "in_progress":
            return {
                "status": "needs_attention",
                "stage": "encounter_episode_pause",
                "message": "Campaign 当前未在进行中；遭遇自动化保持在已固化检查点。",
            }
        if (
            str(current_episode["id"]) != frozen_episode_id
            or int(current_episode["version"]) != frozen_episode_version
        ):
            return {
                "status": "needs_attention",
                "stage": "encounter_episode_changed",
                "message": "Campaign 暂停或恢复边界已经变化；旧回合任务不会越过新版本提交。",
            }
        transitioned = GameplayService(self.repo).command_encounter(
            encounter_id,
            identity,
            GameplayCommand(
                command_id=str(existing["id"]),
                expected_version=int(existing["encounter_version"]),
                command_type=str(prepared["command_type"]),
                payload=dict(prepared["payload"]),
            ),
        )
        completed = self.repo.complete_encounter_automation_turn(
            str(existing["id"]),
            expected_version=int(existing["version"]),
            result=transitioned,
        )
        public_message = self._public_resolution(
            encounter,
            participant,
            completed["selection"],
            transitioned,
        )
        self.repo.create_table_message(
            identity=identity,
            audience="announcement",
            content=public_message,
            recipient_member_id=None,
            client_message_id=f"encounter-automation:{existing['id']}",
        )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=identity.campaign_id,
            audience="session",
            event_type="encounter.automated_turn_committed",
            resource_type="coc7_encounter",
            resource_id=encounter_id,
            payload={
                "participant_id": participant["participant_id"],
                "public_message": public_message,
                "policy": policy,
            },
        )
        follow_up = AutoKpQueueService(self.repo).enqueue_encounter_turn(encounter_id)
        return {
            "status": "succeeded",
            "stage": "encounter_turn",
            "turn": completed,
            "public_message": public_message,
            "follow_up_job_id": follow_up["id"] if follow_up else None,
        }

    async def _selection(
        self,
        encounter: dict[str, Any],
        participant: dict[str, Any],
        *,
        phase: str,
        policy: str,
        director: EnemyTurnDirector,
        source_model: str,
    ) -> dict[str, Any]:
        options = [
            item.as_dict()
            for item in self.turn_agent.options(encounter, participant)
            if item.kind != "improvised"
        ]
        if not options:
            raise ValueError("Ruleset exposes no executable encounter action")
        if phase == "idle_player" and policy in {"skip", "defend"}:
            desired = "defend" if policy == "defend" else "end_turn"
            option = next(
                (item for item in options if item["action_key"] == desired),
                next(item for item in options if item["action_key"] == "end_turn"),
            )
            return {
                "action_key": option["action_key"],
                "target_id": None,
                "public_intent": (
                    "玩家超时，按 Session 0 约定采取防御。"
                    if option["action_key"] == "defend"
                    else "玩家超时，按 Session 0 约定结束本回合。"
                ),
                "reason": f"deterministic idle policy: {policy}",
                "agent": {"used": False, "source_model": "none"},
            }
        snapshot = self._snapshot(encounter, participant, options, phase=phase)
        try:
            proposed = await director.select_enemy_turn(
                campaign_id=str(encounter["campaign_id"]), snapshot=snapshot
            )
            validated = self._validate_selection(
                proposed, options=options, targets=snapshot["targets"]
            )
            return {
                **validated,
                "agent": {
                    "used": True,
                    "prompt_version": PROMPT_VERSION,
                    "source_model": source_model,
                    "fallback": False,
                },
            }
        except CampaignAiCallCancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - safe deterministic liveness fallback
            fallback = next(
                (item for item in options if item["action_key"] == "defend"),
                next(item for item in options if item["action_key"] == "end_turn"),
            )
            return {
                "action_key": fallback["action_key"],
                "target_id": None,
                "public_intent": "对手一时没有抓住进攻机会，转而自保。",
                "reason": f"safe fallback after agent failure: {type(exc).__name__}",
                "agent": {
                    "used": True,
                    "prompt_version": PROMPT_VERSION,
                    "source_model": source_model,
                    "fallback": True,
                },
            }

    def _snapshot(
        self,
        encounter: dict[str, Any],
        participant: dict[str, Any],
        options: list[dict[str, Any]],
        *,
        phase: str,
    ) -> dict[str, Any]:
        targets = self._legal_targets(encounter, participant)
        motivation = "遵循当前遭遇目标并优先自保"
        npc_id = participant.get("npc_id")
        if npc_id:
            try:
                npc = self.repo.get_campaign_npc(
                    str(encounter["campaign_id"]), str(npc_id)
                )
                motivation = str(
                    npc.get("secret_notes") or npc.get("public_notes") or motivation
                )
            except KeyError:
                pass
        return {
            "encounter_kind": encounter["kind"],
            "round_no": encounter["round_no"],
            "phase": phase,
            "actor": {
                "id": participant["participant_id"],
                "name": participant["name"],
                "conditions": [
                    item.get("type") for item in participant.get("conditions") or []
                ],
            },
            "private_motivation": motivation[:1200],
            "allowed_actions": options,
            "targets": targets,
        }

    def _legal_targets(
        self, encounter: dict[str, Any], participant: dict[str, Any]
    ) -> list[dict[str, Any]]:
        candidates = [
            item
            for item in encounter["state"]["participants"]
            if item["participant_id"] != participant["participant_id"]
            and not self._dead(item)
        ]
        side = participant.get("side")
        if side is not None:
            candidates = [item for item in candidates if item.get("side") != side]
        elif not participant.get("investigator_id"):
            investigators = [item for item in candidates if item.get("investigator_id")]
            if investigators:
                candidates = investigators
        else:
            npcs = [item for item in candidates if not item.get("investigator_id")]
            if npcs:
                candidates = npcs
        return [
            {
                "id": str(item["participant_id"]),
                "name": str(item["name"]),
                "conditions": [
                    condition.get("type")
                    for condition in item.get("conditions") or []
                ],
            }
            for item in candidates
        ]

    @staticmethod
    def _validate_selection(
        output: EnemyTurnOutput,
        *,
        options: list[dict[str, Any]],
        targets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        option = next(
            (item for item in options if item["action_key"] == output.action_key),
            None,
        )
        if option is None:
            raise ValueError("Enemy agent selected an action outside the rules catalogue")
        target = next(
            (item for item in targets if item["id"] == output.target_id), None
        )
        if option["target_required"] != (target is not None):
            raise ValueError("Enemy agent target does not match the selected action")
        return {
            "action_key": option["action_key"],
            "target_id": target["id"] if target else None,
            "public_intent": output.public_intent,
            "reason": output.reason,
        }

    def _target(
        self,
        encounter: dict[str, Any],
        participant: dict[str, Any],
        target_id: str | None,
    ) -> dict[str, Any] | None:
        if target_id is None:
            return None
        legal = {item["id"] for item in self._legal_targets(encounter, participant)}
        if target_id not in legal:
            raise ValueError("Prepared encounter target is no longer legal")
        return next(
            item
            for item in encounter["state"]["participants"]
            if item["participant_id"] == target_id
        )

    @staticmethod
    def _public_resolution(
        encounter: dict[str, Any],
        participant: dict[str, Any],
        selection: dict[str, Any],
        transitioned: dict[str, Any],
    ) -> str:
        intent = str(selection.get("public_intent") or f"{participant['name']}采取行动。")
        result = dict((transitioned.get("event") or {}).get("result") or {})
        action = dict(result.get("action") or {})
        outcome = str(action.get("outcome") or "")
        target_id = selection.get("target_id")
        target_name = next(
            (
                str(item["name"])
                for item in encounter["state"]["participants"]
                if item["participant_id"] == target_id
            ),
            "目标",
        )
        if outcome == "defending":
            consequence = f"{participant['name']}稳住架势，进入防守。"
        elif outcome == "passed_turn":
            consequence = f"{participant['name']}没有取得新的进展，本回合结束。"
        elif outcome == "attacker_hits":
            damage = dict(action.get("damage") or {})
            amount = int(damage.get("damage") or 0)
            consequence = f"攻击命中{target_name}，造成 {amount} 点伤害。"
        elif outcome == "defender_hits":
            damage = dict(action.get("damage") or {})
            amount = int(damage.get("damage") or 0)
            consequence = f"{target_name}反击命中，令{participant['name']}受到 {amount} 点伤害。"
        elif outcome in {"attacker_misses", "miss"}:
            consequence = f"攻击没有命中{target_name}。"
        else:
            consequence = "规则结算完成，遭遇状态已推进。"
        if (transitioned.get("encounter") or {}).get("status") == "completed":
            consequence = f"{consequence} 一方已无法继续行动，遭遇结束。"
        return f"{intent} {consequence}".strip()

    @staticmethod
    def _active_participant(encounter: dict[str, Any]) -> dict[str, Any]:
        active_id = encounter["state"]["turn_order"][int(encounter["turn_index"])]
        return next(
            item
            for item in encounter["state"]["participants"]
            if item["participant_id"] == active_id
        )

    @staticmethod
    def _require_phase(participant: dict[str, Any], phase: str) -> None:
        if phase == "enemy" and participant.get("investigator_id"):
            raise ValueError("Enemy automation cannot control an investigator")
        if phase == "idle_player" and not participant.get("investigator_id"):
            raise ValueError("Idle-player automation requires an investigator")

    @staticmethod
    def _require_scope(
        encounter: dict[str, Any], identity: AuthenticatedMember
    ) -> None:
        if (
            identity.role != "kp"
            or encounter["campaign_id"] != identity.campaign_id
            or encounter["session_id"] != identity.session_id
            or encounter["status"] != "active"
        ):
            raise PermissionError("Active KP encounter authority required")

    @staticmethod
    def _dead(participant: dict[str, Any]) -> bool:
        return any(
            item.get("active", True) and item.get("type") == "dead"
            for item in participant.get("conditions") or []
        )

    @staticmethod
    def _safety_pause() -> dict[str, Any]:
        return {
            "status": "needs_attention",
            "stage": "encounter_safety_pause",
            "message": "安全工具已暂停遭遇自动化；处理并恢复后将从同一权威状态继续。",
        }


__all__ = ["EncounterAutomationService"]
