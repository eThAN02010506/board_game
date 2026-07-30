"""Application policy for persistent CoC7 gameplay state machines."""

from __future__ import annotations

import hashlib
import secrets
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ai_kp.application.ports.repositories import GameplayStore
from ai_kp.platform.sessions.models import AuthenticatedMember
from ai_kp.rulesets import get_campaign_ruleset, get_ruleset
from ai_kp.rulesets.coc7.mechanics.gameplay import (
    adjusted_chase_move,
    apply_damage,
    apply_first_aid,
    apply_medicine,
    apply_natural_healing,
    apply_sanity_loss,
    chase_action_points,
    evaluate_recorded_dice,
    resolve_chase_hazard,
    resolve_dying_con,
    resolve_fighting_maneuver,
    resolve_major_wound_con,
    resolve_melee_exchange,
    resolve_skill_development,
    validate_dice_expression,
)
from ai_kp.rulesets.coc7.mechanics.skill_check import success_level

GAMEPLAY_SOURCE_REFERENCE = {
    "source_id": "coc7-keeper-cn-2002c",
    "chapters": ["第五章 游戏系统", "第六章 战斗", "第七章 追逐", "第八章 理智"],
    "page_ranges": [[80, 85], [86, 108], [109, 126], [128, 139]],
}


@dataclass(frozen=True)
class EncounterCreateCommand:
    kind: str
    title: str
    participants: tuple[dict[str, Any], ...]
    locations: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class GameplayCommand:
    command_id: str
    expected_version: int
    command_type: str
    payload: dict[str, Any]
    visibility: str = "table"


class GameplayService:
    def __init__(self, repo: GameplayStore):
        self.repo = repo
        self.ruleset = get_ruleset("coc7")

    def create_encounter(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: EncounterCreateCommand,
    ) -> dict[str, Any]:
        self._require_kp(identity, campaign_id)
        self._require_coc7_campaign(campaign_id)
        if command.kind == "combat":
            state = self._new_combat_state(campaign_id, command.participants)
        elif command.kind == "chase":
            state = self._new_chase_state(
                campaign_id, command.participants, command.locations
            )
        else:
            raise ValueError("Encounter kind must be combat or chase")
        return self.repo.create_coc7_encounter(
            campaign_id=campaign_id,
            session_id=identity.session_id,
            kind=command.kind,
            title=command.title,
            state=state,
            member_id=identity.member_id,
        )

    def list_encounters(
        self, campaign_id: str, identity: AuthenticatedMember
    ) -> list[dict[str, Any]]:
        self._require_campaign(identity, campaign_id)
        return [
            self._project_encounter(item, identity)
            for item in self.repo.list_coc7_encounters(
                campaign_id, identity.session_id
            )
        ]

    def get_encounter(
        self, encounter_id: str, identity: AuthenticatedMember
    ) -> dict[str, Any]:
        encounter = self.repo.get_coc7_encounter(encounter_id)
        self._require_encounter(identity, encounter)
        return self._project_encounter(encounter, identity)

    def list_encounter_events(
        self, encounter_id: str, identity: AuthenticatedMember
    ) -> list[dict[str, Any]]:
        encounter = self.repo.get_coc7_encounter(encounter_id)
        self._require_encounter(identity, encounter)
        events = self.repo.list_coc7_gameplay_events(encounter_id=encounter_id)
        return [
            event
            for event in events
            if identity.role == "kp" or event["visibility"] == "table"
        ]

    def command_encounter(
        self,
        encounter_id: str,
        identity: AuthenticatedMember,
        command: GameplayCommand,
    ) -> dict[str, Any]:
        encounter = self.repo.get_coc7_encounter(encounter_id)
        self._require_kp(identity, str(encounter["campaign_id"]))
        self._require_encounter(identity, encounter)
        if encounter["status"] != "active":
            raise ValueError("Only an active encounter accepts commands")
        state = deepcopy(encounter["state"])
        round_no = int(encounter["round_no"])
        turn_index = int(encounter["turn_index"])
        status = str(encounter["status"])
        if encounter["kind"] == "combat":
            result, round_no, turn_index, status = self._command_combat(
                state,
                round_no,
                turn_index,
                command.command_type,
                command.payload,
            )
        else:
            result, round_no, turn_index, status = self._command_chase(
                state,
                round_no,
                turn_index,
                command.command_type,
                command.payload,
            )
        transitioned = self.repo.transition_coc7_encounter(
            encounter_id,
            expected_version=command.expected_version,
            state=state,
            round_no=round_no,
            turn_index=turn_index,
            status=status,
            member_id=identity.member_id,
            command_id=command.command_id,
            event_type=f"{encounter['kind']}.{command.command_type}",
            command_input=command.payload,
            result=result,
            visibility=command.visibility,
            ruleset_id=self.ruleset.manifest.ruleset_id,
            ruleset_version=self.ruleset.manifest.version,
            source_reference=GAMEPLAY_SOURCE_REFERENCE,
        )
        if not transitioned["idempotent_replay"]:
            linked = self._sync_encounter_health_effect(
                encounter,
                state,
                identity,
                command,
                result,
            )
            if linked is not None:
                transitioned["linked_character_transition"] = linked
        return transitioned

    def get_character_state(
        self,
        campaign_id: str,
        investigator_id: str,
        identity: AuthenticatedMember,
    ) -> dict[str, Any]:
        self._require_campaign(identity, campaign_id)
        record = self.repo.get_campaign_investigator(campaign_id, investigator_id)
        self._require_character_visible(identity, record)
        return {
            "investigator_id": investigator_id,
            "state": record["campaign_state"],
            "events": [
                event
                for event in self.repo.list_coc7_gameplay_events(
                    campaign_id=campaign_id,
                    investigator_id=investigator_id,
                )
                if identity.role == "kp" or event["visibility"] == "table"
            ],
        }

    def command_character(
        self,
        campaign_id: str,
        investigator_id: str,
        identity: AuthenticatedMember,
        command: GameplayCommand,
    ) -> dict[str, Any]:
        self._require_kp(identity, campaign_id)
        self._require_coc7_campaign(campaign_id)
        record = self.repo.get_campaign_investigator(campaign_id, investigator_id)
        state = record.get("campaign_state")
        revision = record.get("approved_revision")
        if state is None or revision is None:
            raise ValueError("CoC7 state commands require an approved investigator")
        canonical = revision["canonical_sheet"]
        result, changes = self._character_transition(
            state, canonical, command.command_type, command.payload
        )
        transitioned = self.repo.transition_coc7_character_state(
            campaign_id=campaign_id,
            session_id=identity.session_id,
            investigator_id=investigator_id,
            expected_version=command.expected_version,
            changes=changes,
            member_id=identity.member_id,
            command_id=command.command_id,
            event_type=f"character.{command.command_type}",
            command_input=command.payload,
            result=result,
            visibility=command.visibility,
            ruleset_id=self.ruleset.manifest.ruleset_id,
            ruleset_version=self.ruleset.manifest.version,
            source_reference=GAMEPLAY_SOURCE_REFERENCE,
        )
        if command.command_type == "development" and not transitioned[
            "idempotent_replay"
        ]:
            transitioned["permanent_change"] = self._propose_growth(
                campaign_id,
                investigator_id,
                identity,
                canonical,
                command.payload,
                result,
            )
        return transitioned

    def _new_combat_state(
        self, campaign_id: str, participants: tuple[dict[str, Any], ...]
    ) -> dict[str, Any]:
        normalized = [
            self._normalize_participant(campaign_id, item, require_move=False)
            for item in participants
        ]
        self._require_unique_participants(normalized)
        if len(normalized) < 2:
            raise ValueError("Combat requires at least two participants")
        ordered = sorted(
            normalized,
            key=lambda item: (
                -(int(item["dex"]) + (50 if item.get("readied_firearm") else 0)),
                str(item["participant_id"]),
            ),
        )
        return {
            "participants": ordered,
            "turn_order": [item["participant_id"] for item in ordered],
            "acted": [],
            "defense_reactions": {},
        }

    def _new_chase_state(
        self,
        campaign_id: str,
        participants: tuple[dict[str, Any], ...],
        locations: tuple[dict[str, Any], ...],
    ) -> dict[str, Any]:
        if len(locations) < 3:
            raise ValueError("A chase requires at least three ordered locations")
        normalized_locations = []
        for index, location in enumerate(locations):
            label = str(location.get("label") or "").strip()
            if not label:
                raise ValueError("Every chase location requires a label")
            normalized_locations.append(
                {
                    "location_index": index,
                    "label": label,
                    "hazard": deepcopy(location.get("hazard")),
                }
            )
        normalized = [
            self._normalize_participant(campaign_id, item, require_move=True)
            for item in participants
        ]
        self._require_unique_participants(normalized)
        if len(normalized) < 2:
            raise ValueError("A chase requires at least two participants")
        if {item.get("chase_role") for item in normalized} != {"pursuer", "fleeing"}:
            raise ValueError("A chase requires both pursuer and fleeing roles")
        for participant in normalized:
            move = adjusted_chase_move(
                int(participant["move"]),
                str(participant.get("con_success_level") or "regular"),  # type: ignore[arg-type]
            )
            participant["adjusted_move"] = move
        slowest = min(int(item["adjusted_move"]) for item in normalized)
        for participant in normalized:
            participant["action_points"] = chase_action_points(
                int(participant["adjusted_move"]), slowest
            )
            participant["location_index"] = max(
                0,
                min(
                    len(normalized_locations) - 1,
                    int(participant.get("location_index") or 0),
                ),
            )
        ordered = sorted(
            normalized, key=lambda item: (-int(item["dex"]), str(item["participant_id"]))
        )
        return {
            "participants": ordered,
            "locations": normalized_locations,
            "turn_order": [item["participant_id"] for item in ordered],
            "slowest_move": slowest,
        }

    def _normalize_participant(
        self, campaign_id: str, raw: dict[str, Any], *, require_move: bool
    ) -> dict[str, Any]:
        participant_id = str(raw.get("participant_id") or "").strip()
        name = str(raw.get("name") or "").strip()
        investigator_id = str(raw.get("investigator_id") or "").strip() or None
        if not participant_id or not name:
            raise ValueError("Every participant requires participant_id and name")
        result = deepcopy(raw)
        if investigator_id:
            record = self.repo.get_campaign_investigator(campaign_id, investigator_id)
            revision = record.get("approved_revision")
            state = record.get("campaign_state")
            if revision is None or state is None:
                raise ValueError("Linked encounter investigators must be approved")
            canonical = revision["canonical_sheet"]
            characteristics = canonical.get("characteristics") or {}
            derived = canonical.get("derived") or {}
            result.update(
                {
                    "investigator_id": investigator_id,
                    "dex": int(characteristics.get("dex") or 0),
                    "move": int(derived.get("move_rate") or derived.get("mov") or 0),
                    "max_hp": int(derived.get("max_hp") or 0),
                    "current_hp": int(state["current_hp"]),
                    "build": int(derived.get("build") or 0),
                    "conditions": deepcopy(state["conditions"]),
                }
            )
        for key in ("dex", "max_hp", "current_hp"):
            if type(result.get(key)) is not int or int(result[key]) < 0:
                raise ValueError(f"Participant {key} must be a non-negative integer")
        if result["max_hp"] <= 0 or result["current_hp"] > result["max_hp"]:
            raise ValueError("Participant hit points are invalid")
        if require_move and (
            type(result.get("move")) is not int or int(result["move"]) < 0
        ):
            raise ValueError("Chase participant MOV must be a non-negative integer")
        result["participant_id"] = participant_id
        result["name"] = name
        result["conditions"] = deepcopy(result.get("conditions") or [])
        result["readied_firearm"] = bool(result.get("readied_firearm", False))
        result["build"] = int(result.get("build") or 0)
        return result

    @staticmethod
    def _require_unique_participants(participants: list[dict[str, Any]]) -> None:
        ids = [str(item["participant_id"]) for item in participants]
        if len(ids) != len(set(ids)):
            raise ValueError("Encounter participant IDs must be unique")

    def _command_combat(
        self,
        state: dict[str, Any],
        round_no: int,
        turn_index: int,
        command_type: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], int, int, str]:
        if command_type in {"complete", "cancel"}:
            return (
                {"status": "completed" if command_type == "complete" else "cancelled"},
                round_no,
                turn_index,
                "completed" if command_type == "complete" else "cancelled",
            )
        if command_type == "advance_turn":
            turn_index += 1
            if turn_index >= len(state["turn_order"]):
                turn_index = 0
                round_no += 1
                state["acted"] = []
                state["defense_reactions"] = {}
            return (
                {
                    "round_no": round_no,
                    "turn_index": turn_index,
                    "active_participant_id": state["turn_order"][turn_index],
                },
                round_no,
                turn_index,
                "active",
            )
        handlers = {
            "melee": self._combat_melee,
            "maneuver": self._combat_maneuver,
            "firearm": self._combat_firearm,
            "damage": self._combat_direct_damage,
        }
        handler = handlers.get(command_type)
        if handler is None:
            raise ValueError("Unsupported combat command")
        attacker = (
            self._participant(state, str(payload.get("attacker_id")))
            if command_type != "damage"
            else None
        )
        if attacker is not None:
            self._require_active_turn(state, turn_index, attacker)
            self._require_can_act(attacker)
        target = self._participant(state, str(payload.get("target_id")))
        result = handler(state, attacker, target, payload)
        if attacker is not None and attacker["participant_id"] not in state["acted"]:
            state["acted"].append(attacker["participant_id"])
        return result, round_no, turn_index, "active"

    def _combat_melee(
        self,
        state: dict[str, Any],
        attacker: dict[str, Any] | None,
        target: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if attacker is None:
            raise ValueError("Melee requires an attacker")
        bonus = self._record_defense_reaction(state, target, payload)
        exchange = resolve_melee_exchange(
            attacker_target=int(payload["attacker_target"]),
            attacker_roll=int(payload["attacker_roll"]),
            defender_target=int(payload["defender_target"]),
            defender_roll=int(payload["defender_roll"]),
            defense=str(payload["defense"]),  # type: ignore[arg-type]
        )
        result = {**exchange, "outnumbered_bonus_dice": bonus}
        if exchange["outcome"] == "attacker_hits":
            result["damage"] = self._damage_participant(target, payload)
        elif exchange["outcome"] == "defender_hits":
            result["damage"] = self._damage_participant(attacker, payload)
        return result

    def _combat_maneuver(
        self,
        state: dict[str, Any],
        attacker: dict[str, Any] | None,
        target: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if attacker is None:
            raise ValueError("A fighting maneuver requires an attacker")
        bonus = self._record_defense_reaction(state, target, payload)
        maneuver = resolve_fighting_maneuver(
            attacker_target=int(payload["attacker_target"]),
            attacker_roll=int(payload["attacker_roll"]),
            attacker_build=int(payload["attacker_build"]),
            defender_target=int(payload["defender_target"]),
            defender_roll=int(payload["defender_roll"]),
            defender_build=int(payload["defender_build"]),
            defense=str(payload["defense"]),  # type: ignore[arg-type]
        )
        effect = str(payload.get("effect") or "").strip()
        result = {
            **maneuver,
            "outnumbered_bonus_dice": bonus,
            "effect": effect,
        }
        if maneuver["maneuver_succeeds"]:
            if not effect:
                raise ValueError("A successful maneuver requires its achieved effect")
            target["conditions"].append(
                {
                    "type": "maneuver_effect",
                    "active": True,
                    "effect": effect,
                    "source_participant_id": attacker["participant_id"],
                }
            )
        elif maneuver["outcome"] == "defender_hits" and (
            "damage" in payload or "damage_expression" in payload
        ):
            result["damage"] = self._damage_participant(attacker, payload)
        return result

    def _combat_firearm(
        self,
        _state: dict[str, Any],
        attacker: dict[str, Any] | None,
        target: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if attacker is None:
            raise ValueError("A firearm attack requires an attacker")
        level = success_level(
            int(payload["attacker_target"]), int(payload["attacker_roll"])
        )
        passed = level not in {"failure", "fumble"}
        result: dict[str, Any] = {
            "passed": passed,
            "success_level": level,
            "push_allowed": False,
        }
        if passed:
            result["damage"] = self._damage_participant(target, payload)
        return result

    def _combat_direct_damage(
        self,
        _state: dict[str, Any],
        _attacker: dict[str, Any] | None,
        target: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {"damage": self._damage_participant(target, payload)}

    @staticmethod
    def _record_defense_reaction(
        state: dict[str, Any],
        target: dict[str, Any],
        payload: dict[str, Any],
    ) -> int:
        reactions = state.setdefault("defense_reactions", {})
        prior = int(reactions.get(target["participant_id"], 0))
        expected_bonus = 1 if prior > 0 else 0
        if int(payload.get("attacker_bonus_dice") or 0) != expected_bonus:
            raise ValueError(
                "Melee bonus dice do not match the defender's outnumbered state"
            )
        reactions[target["participant_id"]] = prior + 1
        return expected_bonus

    def _command_chase(
        self,
        state: dict[str, Any],
        round_no: int,
        turn_index: int,
        command_type: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], int, int, str]:
        if command_type in {"complete", "cancel"}:
            return (
                {"status": "completed" if command_type == "complete" else "cancelled"},
                round_no,
                turn_index,
                "completed" if command_type == "complete" else "cancelled",
            )
        if command_type == "advance_turn":
            turn_index += 1
            if turn_index >= len(state["turn_order"]):
                turn_index = 0
                round_no += 1
                for item in state["participants"]:
                    item["action_points"] = chase_action_points(
                        int(item["adjusted_move"]), int(state["slowest_move"])
                    )
            return (
                {
                    "round_no": round_no,
                    "turn_index": turn_index,
                    "active_participant_id": state["turn_order"][turn_index],
                },
                round_no,
                turn_index,
                "active",
            )
        participant = self._participant(state, str(payload.get("participant_id")))
        self._require_active_turn(state, turn_index, participant)
        self._require_can_act(participant)
        if command_type == "move":
            steps = int(payload.get("steps") or 1)
            if steps < 1 or steps > int(participant["action_points"]):
                raise ValueError("Chase movement exceeds available action points")
            destination = int(participant["location_index"]) + (
                steps if payload.get("direction", "forward") == "forward" else -steps
            )
            if not 0 <= destination < len(state["locations"]):
                raise ValueError("Chase movement leaves the location track")
            participant["location_index"] = destination
            participant["action_points"] -= steps
            result = {
                "participant_id": participant["participant_id"],
                "location_index": destination,
                "action_points_remaining": participant["action_points"],
            }
        elif command_type == "hazard":
            result = resolve_chase_hazard(
                action_points=int(participant["action_points"]),
                passed=bool(payload["passed"]),
                failure_action_cost=int(payload.get("failure_action_cost") or 0),
                damage=int(payload.get("damage") or 0),
            )
            participant["action_points"] = result["action_points_remaining"]
            if result["damage"]:
                result["health"] = self._damage_participant(
                    participant, {"damage": result["damage"]}
                )
        else:
            raise ValueError("Unsupported chase command")
        return result, round_no, turn_index, "active"

    def _character_transition(
        self,
        state: dict[str, Any],
        canonical: dict[str, Any],
        command_type: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if command_type in {
            "damage",
            "major_wound_con",
            "dying_con",
            "first_aid",
            "medicine",
            "natural_healing",
        }:
            return self._health_transition(
                state, canonical, command_type, payload
            )
        if command_type in {"sanity", "end_bout", "reset_san_day"}:
            return self._sanity_transition(
                state, canonical, command_type, payload
            )
        if command_type == "development":
            return self._development_transition(state, canonical, payload)
        raise ValueError("Unsupported CoC7 character command")

    def _health_transition(
        self,
        state: dict[str, Any],
        canonical: dict[str, Any],
        command_type: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        derived = canonical.get("derived") or {}
        characteristics = canonical.get("characteristics") or {}
        max_hp = int(derived.get("max_hp") or 0)
        if command_type == "damage":
            amount, dice = self._recorded_value(payload, "damage")
            result = apply_damage(
                current_hp=int(state["current_hp"]),
                max_hp=max_hp,
                damage=amount,
                conditions=state["conditions"],
            )
            result["dice"] = dice
            return result, {
                "current_hp": result["current_hp"],
                "conditions": result["conditions"],
            }
        if command_type == "major_wound_con":
            passed, roll = self._passed_check(
                payload, int(characteristics.get("con") or 0)
            )
            conditions = resolve_major_wound_con(state["conditions"], passed=passed)
            return {"passed": passed, "roll": roll, "conditions": conditions}, {
                "conditions": conditions
            }
        if command_type == "dying_con":
            passed, roll = self._passed_check(
                payload, int(characteristics.get("con") or 0)
            )
            conditions = resolve_dying_con(state["conditions"], passed=passed)
            return {"passed": passed, "roll": roll, "conditions": conditions}, {
                "conditions": conditions
            }
        if command_type == "first_aid":
            result = apply_first_aid(
                current_hp=int(state["current_hp"]),
                max_hp=max_hp,
                conditions=state["conditions"],
                passed=bool(payload["passed"]),
            )
            return result, {
                "current_hp": result["current_hp"],
                "conditions": result["conditions"],
            }
        if command_type == "medicine":
            healing, dice = self._recorded_value(payload, "healing")
            result = apply_medicine(
                current_hp=int(state["current_hp"]),
                max_hp=max_hp,
                conditions=state["conditions"],
                passed=bool(payload["passed"]),
                healing=healing,
            )
            result["dice"] = dice
            return result, {
                "current_hp": result["current_hp"],
                "conditions": result["conditions"],
            }
        return self._natural_healing_transition(state, max_hp, payload)

    def _natural_healing_transition(
        self,
        state: dict[str, Any],
        max_hp: int,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        has_major_wound = any(
            item.get("type") == "major_wound" and item.get("active", True)
            for item in state["conditions"]
        )
        level = (
            str(payload.get("con_success_level") or "failure")
            if has_major_wound
            else None
        )
        healing_rolls = payload.get("healing_rolls")
        if has_major_wound and healing_rolls is None and level not in {
            "failure",
            "fumble",
        }:
            count = 2 if level in {"extreme", "critical"} else 1
            healing_rolls = [secrets.randbelow(3) + 1 for _ in range(count)]
        result = apply_natural_healing(
            current_hp=int(state["current_hp"]),
            max_hp=max_hp,
            conditions=state["conditions"],
            period_id=str(payload.get("period_id") or ""),
            days=int(payload.get("days") or 1),
            con_success_level=level,  # type: ignore[arg-type]
            healing_rolls=(
                [int(item) for item in healing_rolls]
                if healing_rolls is not None
                else None
            ),
        )
        result["dice"] = {
            "con_success_level": level,
            "healing_rolls": healing_rolls or [],
        }
        return result, {
            "current_hp": result["current_hp"],
            "conditions": result["conditions"],
        }

    def _sanity_transition(
        self,
        state: dict[str, Any],
        canonical: dict[str, Any],
        command_type: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if command_type == "end_bout":
            conditions = [
                item
                for item in state["conditions"]
                if item.get("type") != "bout_of_madness"
            ]
            return {"bout_ended": True, "conditions": conditions}, {
                "conditions": conditions
            }
        if command_type == "reset_san_day":
            return {
                "daily_san_loss": 0,
                "daily_san_start": int(state["current_san"]),
            }, {
                "daily_san_loss": 0,
                "daily_san_start": int(state["current_san"]),
                "last_san_day": str(payload.get("day") or ""),
            }
        derived = canonical.get("derived") or {}
        characteristics = canonical.get("characteristics") or {}
        max_san = int(derived.get("max_san", 99))
        if command_type == "sanity":
            success_loss, success_dice = self._recorded_value(
                payload, "success_loss"
            )
            failure_loss, failure_dice = self._recorded_value(
                payload, "failure_loss"
            )
            san_roll = int(payload.get("sanity_roll") or self._d100())
            projected_loss = success_loss if san_roll <= int(state["current_san"]) else failure_loss
            int_roll = payload.get("intelligence_roll")
            if projected_loss >= 5 and int_roll is None:
                int_roll = self._d100()
            initial = int(state.get("daily_san_start") or state["current_san"])
            first = apply_sanity_loss(
                current_san=int(state["current_san"]),
                maximum_san=max_san,
                intelligence=int(characteristics.get("int") or 0),
                roll=san_roll,
                success_loss=success_loss,
                failure_loss=failure_loss,
                daily_loss_before=int(state.get("daily_san_loss") or 0),
                daily_starting_san=initial,
                intelligence_roll=int(int_roll) if int_roll is not None else None,
            )
            bout_roll = payload.get("bout_roll")
            bout_duration = payload.get("bout_duration")
            if first["requires_bout"]:
                bout_roll = int(bout_roll or secrets.randbelow(10) + 1)
                bout_duration = int(bout_duration or secrets.randbelow(10) + 1)
                first = apply_sanity_loss(
                    current_san=int(state["current_san"]),
                    maximum_san=max_san,
                    intelligence=int(characteristics.get("int") or 0),
                    roll=san_roll,
                    success_loss=success_loss,
                    failure_loss=failure_loss,
                    daily_loss_before=int(state.get("daily_san_loss") or 0),
                    daily_starting_san=initial,
                    intelligence_roll=int(int_roll) if int_roll is not None else None,
                    bout_roll=bout_roll,
                    bout_duration=bout_duration,
                )
            conditions = self._merge_insanity_conditions(state["conditions"], first)
            first["dice"] = {
                "success_loss": success_dice,
                "failure_loss": failure_dice,
                "sanity_roll": san_roll,
                "intelligence_roll": int_roll,
                "bout_roll": bout_roll,
                "bout_duration": bout_duration,
            }
            return first, {
                "current_san": first["current_san"],
                "daily_san_loss": first["daily_san_loss"],
                "daily_san_start": initial,
                "conditions": conditions,
            }
        raise ValueError("Unsupported CoC7 sanity command")

    def _development_transition(
        self,
        state: dict[str, Any],
        canonical: dict[str, Any],
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        skill = self._find_skill(canonical, payload, state["conditions"])
        development_roll = int(payload.get("development_roll") or self._d100())
        improving = (
            development_roll > int(skill["current_value"]) or development_roll > 95
        )
        increase_roll = payload.get("increase_roll")
        if improving and increase_roll is None:
            increase_roll = secrets.randbelow(10) + 1
        result = resolve_skill_development(
            current_value=int(skill["current_value"]),
            development_roll=development_roll,
            increase_roll=int(increase_roll) if increase_roll is not None else None,
        )
        result.update(
            {
                "skill_key": skill["skill_key"],
                "display_name": skill["display_name"],
                "specialization": skill.get("specialization"),
                "requires_player_confirmation": True,
            }
        )
        conditions = [
            item
            for item in state["conditions"]
            if not (
                item.get("type") in {
                    "development_pending",
                    "skill_growth_mark",
                }
                and self._same_skill_condition(item, skill)
            )
        ]
        conditions.append(
            {
                "type": "development_pending",
                "active": True,
                "skill_key": skill["skill_key"],
                "specialization": skill.get("specialization"),
                "old_value": result["old_value"],
                "new_value": result["new_value"],
                "improved": result["improved"],
            }
        )
        return result, {"conditions": conditions}

    def _propose_growth(
        self,
        campaign_id: str,
        investigator_id: str,
        identity: AuthenticatedMember,
        canonical: dict[str, Any],
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        record = self.repo.get_campaign_investigator(campaign_id, investigator_id)
        actor_id = record.get("legacy_pc_id")
        event = self.repo.append_event(
            campaign_id,
            "pc",
            "coc7.skill_development",
            f"{result['display_name']} 成长检定：{result['old_value']} → {result['new_value']}",
            actor_id=actor_id,
            visibility="table",
            payload={
                "skill_key": result["skill_key"],
                "development_roll": result["development_roll"],
                "increase": result["increase"],
            },
        )
        change = {
            "skill_key": result["skill_key"],
            "new_value": result["new_value"],
        }
        specialization = str(result.get("specialization") or "").strip()
        if specialization:
            change["specialization"] = specialization
        return self.repo.create_permanent_change_proposal(
            campaign_id=campaign_id,
            investigator_id=investigator_id,
            kind="skill",
            summary=(
                f"{result['display_name']} 成长至 {result['new_value']}"
                if result["improved"]
                else f"{result['display_name']} 未成长，清除本次成长标记"
            ),
            change=change,
            source_event_id=event["id"],
            rationale=(
                "CoC7 幕间成长：结算已标记技能并清除本次成长标记；"
                "仅当成长骰高于当前技能值（或骰值大于 95）时增加 1D10 点。"
            ),
            proposed_by_member_id=identity.member_id,
        )

    @staticmethod
    def _participant(state: dict[str, Any], participant_id: str) -> dict[str, Any]:
        match = next(
            (
                item
                for item in state["participants"]
                if item["participant_id"] == participant_id
            ),
            None,
        )
        if match is None:
            raise ValueError("Encounter participant was not found")
        return match

    def _damage_participant(
        self, participant: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        amount, dice = self._recorded_value(payload, "damage")
        health = apply_damage(
            current_hp=int(participant["current_hp"]),
            max_hp=int(participant["max_hp"]),
            damage=amount,
            conditions=participant.get("conditions"),
        )
        participant["current_hp"] = health["current_hp"]
        participant["conditions"] = health["conditions"]
        return {**health, "dice": dice, "participant_id": participant["participant_id"]}

    def _sync_encounter_health_effect(
        self,
        encounter: dict[str, Any],
        encounter_state: dict[str, Any],
        identity: AuthenticatedMember,
        command: GameplayCommand,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        damage_result = result.get("damage") or result.get("health")
        if not isinstance(damage_result, dict) or int(damage_result.get("damage") or 0) <= 0:
            return None
        participant = self._participant(
            encounter_state, str(damage_result["participant_id"])
        )
        investigator_id = participant.get("investigator_id")
        if not investigator_id:
            return None
        record = self.repo.get_campaign_investigator(
            str(encounter["campaign_id"]), str(investigator_id)
        )
        character_state = record.get("campaign_state")
        revision = record.get("approved_revision")
        if character_state is None or revision is None:
            raise ValueError("Linked encounter investigator no longer has approved state")
        max_hp = int(
            (revision["canonical_sheet"].get("derived") or {}).get("max_hp") or 0
        )
        applied = apply_damage(
            current_hp=int(character_state["current_hp"]),
            max_hp=max_hp,
            damage=int(damage_result["damage"]),
            conditions=character_state["conditions"],
        )
        linked_command_id = "encounter-state-" + hashlib.sha256(
            f"{encounter['id']}:{command.command_id}".encode()
        ).hexdigest()
        return self.repo.transition_coc7_character_state(
            campaign_id=str(encounter["campaign_id"]),
            session_id=str(encounter["session_id"]),
            investigator_id=str(investigator_id),
            expected_version=int(character_state["state_version"]),
            changes={
                "current_hp": applied["current_hp"],
                "conditions": applied["conditions"],
            },
            member_id=identity.member_id,
            command_id=linked_command_id,
            event_type="character.encounter_damage",
            command_input={
                "encounter_id": encounter["id"],
                "encounter_command_id": command.command_id,
                "damage": damage_result["damage"],
            },
            result=applied,
            visibility=command.visibility,
            ruleset_id=self.ruleset.manifest.ruleset_id,
            ruleset_version=self.ruleset.manifest.version,
            source_reference=GAMEPLAY_SOURCE_REFERENCE,
        )

    @staticmethod
    def _merge_insanity_conditions(
        conditions: list[dict[str, Any]], result: dict[str, Any]
    ) -> list[dict[str, Any]]:
        merged = {
            str(item.get("type")): deepcopy(item)
            for item in conditions
            if item.get("type")
        }
        if result["temporary_insanity"]:
            merged["temporary_insanity"] = {
                "type": "temporary_insanity",
                "active": True,
                "duration_unit": "hour",
                "duration": None,
            }
        if result["indefinite_insanity"]:
            merged["indefinite_insanity"] = {
                "type": "indefinite_insanity",
                "active": True,
            }
        if result["permanent_insanity"]:
            merged["permanent_insanity"] = {
                "type": "permanent_insanity",
                "active": True,
            }
        if result["bout"]:
            merged["bout_of_madness"] = result["bout"]
        return list(merged.values())

    @staticmethod
    def _find_skill(
        canonical: dict[str, Any],
        payload: dict[str, Any],
        conditions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        skill_key = str(payload.get("skill_key") or "")
        specialization = str(payload.get("specialization") or "").strip()
        matches = [
            item
            for item in canonical.get("skills") or []
            if item.get("skill_key") == skill_key
            and (
                not specialization
                or str(item.get("specialization") or "").strip() == specialization
            )
        ]
        if len(matches) != 1:
            raise ValueError("Development command must match exactly one skill")
        runtime_marked = any(
            item.get("type") == "skill_growth_mark"
            and GameplayService._same_skill_condition(item, matches[0])
            for item in conditions
        )
        pending = any(
            item.get("type") == "development_pending"
            and GameplayService._same_skill_condition(item, matches[0])
            for item in conditions
        )
        if not runtime_marked and (not matches[0].get("growth_mark") or pending):
            raise ValueError("Only a skill marked during play may make a growth check")
        return matches[0]

    @staticmethod
    def _same_skill_condition(
        condition: dict[str, Any], skill: dict[str, Any]
    ) -> bool:
        return condition.get("skill_key") == skill.get("skill_key") and str(
            condition.get("specialization") or ""
        ).strip() == str(skill.get("specialization") or "").strip()

    @staticmethod
    def _require_active_turn(
        state: dict[str, Any],
        turn_index: int,
        participant: dict[str, Any],
    ) -> None:
        active_id = state["turn_order"][turn_index]
        if participant["participant_id"] != active_id:
            raise ValueError("Only the active encounter participant may take this action")

    @staticmethod
    def _require_can_act(participant: dict[str, Any]) -> None:
        inactive = {"dead", "unconscious", "dying"}
        if any(
            item.get("active", True) and item.get("type") in inactive
            for item in participant.get("conditions") or []
        ):
            raise ValueError("This encounter participant cannot act in their current state")

    @staticmethod
    def _recorded_value(
        payload: dict[str, Any], prefix: str
    ) -> tuple[int, dict[str, Any]]:
        direct = payload.get(prefix)
        expression = payload.get(f"{prefix}_expression")
        rolls = payload.get(f"{prefix}_rolls")
        if direct is not None:
            value = int(direct)
            if value < 0:
                raise ValueError(f"{prefix} cannot be negative")
            return value, {"expression": str(value), "rolls": [], "total": value}
        if expression is None:
            raise ValueError(f"{prefix} or {prefix}_expression is required")
        count, sides, _modifier = validate_dice_expression(str(expression))
        recorded = (
            [int(item) for item in rolls]
            if rolls is not None
            else [secrets.randbelow(sides) + 1 for _ in range(count)]
        )
        value = evaluate_recorded_dice(str(expression), recorded)
        if value < 0:
            raise ValueError(f"{prefix} total cannot be negative")
        return value, {
            "expression": str(expression),
            "rolls": recorded,
            "total": value,
        }

    @staticmethod
    def _passed_check(payload: dict[str, Any], target: int) -> tuple[bool, int | None]:
        if "passed" in payload:
            return bool(payload["passed"]), None
        roll = int(payload.get("roll") or GameplayService._d100())
        return roll <= target, roll

    @staticmethod
    def _d100() -> int:
        return secrets.randbelow(100) + 1

    def _require_coc7_campaign(self, campaign_id: str) -> None:
        campaign = self.repo.get_campaign(campaign_id)
        ruleset = get_campaign_ruleset(campaign)
        if ruleset.manifest.slug != "coc7":
            raise ValueError("This gameplay state machine currently supports CoC7 only")

    @staticmethod
    def _require_campaign(
        identity: AuthenticatedMember, campaign_id: str
    ) -> None:
        if identity.campaign_id != campaign_id:
            raise KeyError(f"Campaign not found: {campaign_id}")

    @classmethod
    def _require_kp(
        cls, identity: AuthenticatedMember, campaign_id: str
    ) -> None:
        cls._require_campaign(identity, campaign_id)
        if identity.role != "kp":
            raise PermissionError("Only the KP may change authoritative CoC7 state")

    @staticmethod
    def _require_encounter(
        identity: AuthenticatedMember, encounter: dict[str, Any]
    ) -> None:
        if (
            identity.campaign_id != encounter["campaign_id"]
            or identity.session_id != encounter["session_id"]
        ):
            raise KeyError(f"CoC7 encounter not found: {encounter['id']}")

    @staticmethod
    def _require_character_visible(
        identity: AuthenticatedMember, record: dict[str, Any]
    ) -> None:
        if identity.role == "kp":
            return
        if identity.pc_id != record.get("legacy_pc_id"):
            raise KeyError(f"Investigator not found: {record['investigator_id']}")

    @staticmethod
    def _project_encounter(
        encounter: dict[str, Any], identity: AuthenticatedMember
    ) -> dict[str, Any]:
        if identity.role == "kp":
            return encounter
        projected = deepcopy(encounter)
        for participant in projected["state"].get("participants", []):
            if participant.get("investigator_id") is None:
                participant.pop("current_hp", None)
                participant.pop("max_hp", None)
                participant.pop("conditions", None)
        return projected


__all__ = [
    "GAMEPLAY_SOURCE_REFERENCE",
    "EncounterCreateCommand",
    "GameplayCommand",
    "GameplayService",
]
