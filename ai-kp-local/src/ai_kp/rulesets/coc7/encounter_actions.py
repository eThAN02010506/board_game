"""CoC7 encounter action catalogue and deterministic command preparation.

This adapter is intentionally model-free. A language agent may select one of
these options, but only this ruleset-owned adapter can turn that selection into
an executable command with replayable dice evidence.
"""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass
from typing import Any

from ai_kp.rulesets.coc7.mechanics.gameplay import validate_dice_expression


@dataclass(frozen=True)
class EncounterActionOption:
    action_key: str
    label: str
    kind: str
    target_required: bool
    description: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PreparedEncounterCommand:
    command_type: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"command_type": self.command_type, "payload": self.payload}


class Coc7EncounterTurnAgent:
    """Ruleset specialist that exposes bounded choices to players or AI."""

    def options(
        self, encounter: dict[str, Any], participant: dict[str, Any]
    ) -> tuple[EncounterActionOption, ...]:
        if encounter["status"] != "active":
            return ()
        result: list[EncounterActionOption] = []
        if encounter["kind"] == "combat":
            for profile in participant.get("action_profiles") or []:
                kind = str(profile.get("kind") or "")
                if kind not in {"melee", "firearm"}:
                    continue
                result.append(
                    EncounterActionOption(
                        action_key=str(profile["action_key"]),
                        label=str(profile["label"]),
                        kind=kind,
                        target_required=True,
                        description=(
                            "选择一名可见目标；确认后由 CoC7 规则引擎固化"
                            "攻防骰和伤害证据。"
                        ),
                    )
                )
            result.append(
                EncounterActionOption(
                    action_key="defend",
                    label="采取防御姿态",
                    kind="defend",
                    target_required=False,
                    description="不替玩家选择攻击目标，记录防御后结束本回合。",
                )
            )
        else:
            action_points = int(participant.get("action_points") or 0)
            if action_points > 0:
                result.extend(
                    (
                        EncounterActionOption(
                            action_key="move_forward",
                            label="向前移动 1 段",
                            kind="move",
                            target_required=False,
                            description="消耗 1 点追逐行动点。",
                        ),
                        EncounterActionOption(
                            action_key="move_backward",
                            label="向后移动 1 段",
                            kind="move",
                            target_required=False,
                            description="消耗 1 点追逐行动点。",
                        ),
                    )
                )
        result.extend(
            (
                EncounterActionOption(
                    action_key="end_turn",
                    label="结束本回合",
                    kind="end_turn",
                    target_required=False,
                    description="不再消耗其他资源，进入下一参与者回合。",
                ),
                EncounterActionOption(
                    action_key="improvised",
                    label="规则外 / Rule of Cool",
                    kind="improvised",
                    target_required=False,
                    description="保留自由描述，由遭遇意图 Agent 提出可确认的规则方案。",
                ),
            )
        )
        return tuple(result)

    def prepare(
        self,
        encounter: dict[str, Any],
        participant: dict[str, Any],
        *,
        action_key: str,
        target: dict[str, Any] | None,
        proposal: dict[str, Any] | None = None,
    ) -> PreparedEncounterCommand:
        if action_key.startswith("maneuver:"):
            if encounter["kind"] != "combat" or target is None:
                raise ValueError("A fighting maneuver requires a combat target")
            profile = self._profile(participant, action_key.removeprefix("maneuver:"))
            effect = str((proposal or {}).get("maneuver_effect") or "").strip()
            if not effect:
                raise ValueError("A fighting maneuver requires a bounded effect")
            return PreparedEncounterCommand(
                command_type="take_turn",
                payload={
                    "participant_id": participant["participant_id"],
                    "inner": {
                        "action_type": "maneuver",
                        "attacker_id": participant["participant_id"],
                        "target_id": target["participant_id"],
                        "attacker_target": int(profile["skill_target"]),
                        "attacker_roll": self._d100(),
                        "attacker_build": int(participant.get("build") or 0),
                        "defender_target": int(target.get("dodge_target") or 25),
                        "defender_roll": self._d100(),
                        "defender_build": int(target.get("build") or 0),
                        "defense": "dodge",
                        "effect": effect,
                    },
                },
            )
        option = next(
            (item for item in self.options(encounter, participant) if item.action_key == action_key),
            None,
        )
        if option is None or option.kind == "improvised":
            raise ValueError("Encounter action is not directly executable")
        inner: dict[str, Any]
        if option.kind == "end_turn":
            inner = {"action_type": "pass", "participant_id": participant["participant_id"]}
        elif option.kind == "defend":
            inner = {"action_type": "defend", "participant_id": participant["participant_id"]}
        elif option.kind == "move":
            inner = {
                "action_type": "move",
                "participant_id": participant["participant_id"],
                "steps": 1,
                "direction": "forward" if action_key == "move_forward" else "backward",
            }
        else:
            if target is None:
                raise ValueError("This encounter action requires a target")
            profile = self._profile(participant, action_key)
            attacker_roll = self._d100()
            if option.kind == "melee":
                reactions = encounter["state"].get("defense_reactions") or {}
                inner = {
                    "action_type": "melee",
                    "attacker_id": participant["participant_id"],
                    "target_id": target["participant_id"],
                    "attacker_target": int(profile["skill_target"]),
                    "attacker_roll": attacker_roll,
                    "defender_target": int(target.get("dodge_target") or 25),
                    "defender_roll": self._d100(),
                    "defense": "dodge",
                    "attacker_bonus_dice": 1 if reactions.get(target["participant_id"]) else 0,
                    **self._damage_evidence(str(profile.get("damage_expression") or "1d3")),
                }
            else:
                inner = {
                    "action_type": "firearm",
                    "attacker_id": participant["participant_id"],
                    "target_id": target["participant_id"],
                    "attacker_target": int(profile["skill_target"]),
                    "attacker_roll": attacker_roll,
                    **self._damage_evidence(str(profile.get("damage_expression") or "1d6")),
                }
        return PreparedEncounterCommand(
            command_type="take_turn",
            payload={"participant_id": participant["participant_id"], "inner": inner},
        )

    @staticmethod
    def _profile(participant: dict[str, Any], action_key: str) -> dict[str, Any]:
        matches = [
            item
            for item in participant.get("action_profiles") or []
            if item.get("action_key") == action_key
        ]
        if len(matches) != 1:
            raise ValueError("Encounter action profile changed")
        return matches[0]

    @staticmethod
    def _d100() -> int:
        return secrets.randbelow(100) + 1

    @staticmethod
    def _damage_evidence(expression: str) -> dict[str, Any]:
        parsed = validate_dice_expression(expression)
        rolls, _terms = parsed.roll(secrets.randbelow)
        return {
            "damage_expression": parsed.normalized,
            "damage_rolls": rolls,
        }


__all__ = [
    "Coc7EncounterTurnAgent",
    "EncounterActionOption",
    "PreparedEncounterCommand",
]
