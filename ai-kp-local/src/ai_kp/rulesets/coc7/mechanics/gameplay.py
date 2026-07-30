"""Pure, deterministic CoC7 gameplay transitions.

The functions in this module never read a database or generate random values.
Callers supply recorded dice results, which makes every transition replayable.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Literal

from ai_kp.rulesets.coc7.mechanics.skill_check import SuccessLevel, success_level

DefenseChoice = Literal["dodge", "fight_back"]

_SUCCESS_RANK: dict[SuccessLevel, int] = {
    "fumble": -1,
    "failure": 0,
    "regular": 1,
    "hard": 2,
    "extreme": 3,
    "critical": 4,
}
_DICE_EXPRESSION = re.compile(
    r"^\s*(?:(?P<count>[1-9]\d*)d(?P<sides>[1-9]\d*)|(?P<fixed>\d+))"
    r"\s*(?P<modifier>[+-]\s*\d+)?\s*$",
    re.IGNORECASE,
)


def validate_dice_expression(expression: str) -> tuple[int, int, int]:
    """Return ``(count, sides, modifier)`` for a bounded damage/SAN expression."""

    match = _DICE_EXPRESSION.fullmatch(expression)
    if match is None:
        raise ValueError("Dice expression must look like 1d6, 2d10+3, or 4")
    if match.group("fixed") is not None:
        fixed = int(match.group("fixed"))
        if fixed > 10_000:
            raise ValueError("Fixed dice value is too large")
        return 0, 0, fixed
    count = int(match.group("count"))
    sides = int(match.group("sides"))
    modifier = int((match.group("modifier") or "0").replace(" ", ""))
    if count > 100 or sides > 1_000 or abs(modifier) > 10_000:
        raise ValueError("Dice expression exceeds safety limits")
    return count, sides, modifier


def evaluate_recorded_dice(expression: str, rolls: list[int]) -> int:
    """Evaluate an expression from explicitly recorded die faces."""

    count, sides, modifier = validate_dice_expression(expression)
    if count == 0:
        if rolls:
            raise ValueError("A fixed value does not accept die rolls")
        return modifier
    if len(rolls) != count:
        raise ValueError(f"Expected {count} recorded dice, received {len(rolls)}")
    if any(type(roll) is not int or not 1 <= roll <= sides for roll in rolls):
        raise ValueError(f"Every recorded die must be between 1 and {sides}")
    return sum(rolls) + modifier


def resolve_melee_exchange(
    *,
    attacker_target: int,
    attacker_roll: int,
    defender_target: int,
    defender_roll: int,
    defense: DefenseChoice,
) -> dict[str, Any]:
    """Resolve attack versus dodge/fight-back using CoC7's asymmetric ties."""

    attacker_level = success_level(attacker_target, attacker_roll)
    defender_level = success_level(defender_target, defender_roll)
    attacker_rank = _SUCCESS_RANK[attacker_level]
    defender_rank = _SUCCESS_RANK[defender_level]
    if attacker_rank <= 0 and defender_rank <= 0:
        outcome = "miss"
    elif defense == "dodge":
        outcome = "attacker_hits" if attacker_rank > defender_rank else "defender_avoids"
    elif defense == "fight_back":
        outcome = (
            "defender_hits"
            if defender_rank > attacker_rank
            else "attacker_hits"
            if attacker_rank > 0
            else "miss"
        )
    else:
        raise ValueError("Defense must be dodge or fight_back")
    return {
        "outcome": outcome,
        "defense": defense,
        "attacker_success_level": attacker_level,
        "defender_success_level": defender_level,
        "extreme_damage": outcome == "attacker_hits"
        and attacker_level in {"extreme", "critical"},
        "push_allowed": False,
    }


def resolve_fighting_maneuver(
    *,
    attacker_target: int,
    attacker_roll: int,
    attacker_build: int,
    defender_target: int,
    defender_roll: int,
    defender_build: int,
    defense: DefenseChoice,
) -> dict[str, Any]:
    """Resolve a non-damaging maneuver and report its required penalty dice."""

    build_difference = defender_build - attacker_build
    penalty_dice = max(0, min(2, build_difference))
    if build_difference >= 3:
        return {
            "outcome": "ineffective",
            "maneuver_succeeds": False,
            "build_difference": build_difference,
            "penalty_dice": penalty_dice,
            "push_allowed": False,
        }
    exchange = resolve_melee_exchange(
        attacker_target=attacker_target,
        attacker_roll=attacker_roll,
        defender_target=defender_target,
        defender_roll=defender_roll,
        defense=defense,
    )
    return {
        **exchange,
        "maneuver_succeeds": exchange["outcome"] == "attacker_hits",
        "build_difference": build_difference,
        "penalty_dice": penalty_dice,
    }


def apply_damage(
    *,
    current_hp: int,
    max_hp: int,
    damage: int,
    conditions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply one injury and return the complete resulting health state."""

    if max_hp <= 0 or not 0 <= current_hp <= max_hp:
        raise ValueError("Hit point state is invalid")
    if damage < 0:
        raise ValueError("Damage cannot be negative")
    updated = _condition_map(conditions)
    if damage > 0:
        # A new injury opens a new treatment episode. Treatment remains
        # idempotent inside one episode, but may be attempted after later harm.
        updated.pop("first_aid_applied", None)
        updated.pop("medicine_applied", None)
    instant_death = damage >= max_hp and damage > 0
    major_wound = damage * 2 >= max_hp and damage > 0
    hp = max(0, current_hp - damage)
    if major_wound:
        updated["major_wound"] = {"type": "major_wound", "active": True}
    if instant_death:
        updated["dead"] = {"type": "dead", "active": True, "cause": "massive_damage"}
        updated.pop("dying", None)
        updated["unconscious"] = {"type": "unconscious", "active": True}
    elif hp == 0:
        updated["unconscious"] = {"type": "unconscious", "active": True}
        if "major_wound" in updated:
            updated["dying"] = {
                "type": "dying",
                "active": True,
                "stabilized": False,
            }
    return {
        "current_hp": hp,
        "conditions": list(updated.values()),
        "damage": damage,
        "major_wound": major_wound,
        "instant_death": instant_death,
        "requires_major_wound_con_check": major_wound and not instant_death and hp > 0,
        "requires_dying_con_check": "dying" in updated
        and not bool(updated["dying"].get("stabilized")),
    }


def resolve_major_wound_con(
    conditions: list[dict[str, Any]], *, passed: bool
) -> list[dict[str, Any]]:
    updated = _condition_map(conditions)
    if "major_wound" not in updated:
        raise ValueError("A major-wound CON check requires an active major wound")
    if not passed:
        updated["unconscious"] = {
            "type": "unconscious",
            "active": True,
            "cause": "major_wound",
        }
    return list(updated.values())


def resolve_dying_con(
    conditions: list[dict[str, Any]], *, passed: bool
) -> list[dict[str, Any]]:
    updated = _condition_map(conditions)
    dying = updated.get("dying")
    if dying is None or dying.get("stabilized"):
        raise ValueError("A dying CON check requires an unstabilized dying condition")
    if not passed:
        updated["dead"] = {"type": "dead", "active": True, "cause": "dying"}
        updated.pop("dying", None)
    return list(updated.values())


def apply_first_aid(
    *,
    current_hp: int,
    max_hp: int,
    conditions: list[dict[str, Any]],
    passed: bool,
) -> dict[str, Any]:
    updated = _condition_map(conditions)
    if "first_aid_applied" in updated:
        raise ValueError("First Aid has already succeeded for this injury episode")
    if not passed:
        return {"current_hp": current_hp, "conditions": list(updated.values())}
    updated["first_aid_applied"] = {
        "type": "first_aid_applied",
        "active": True,
    }
    dying = updated.get("dying")
    if dying is not None:
        updated["dying"] = {**dying, "stabilized": True}
        return {"current_hp": max(1, current_hp), "conditions": list(updated.values())}
    return {
        "current_hp": min(max_hp, current_hp + 1),
        "conditions": list(updated.values()),
    }


def apply_medicine(
    *,
    current_hp: int,
    max_hp: int,
    conditions: list[dict[str, Any]],
    passed: bool,
    healing: int,
) -> dict[str, Any]:
    if healing < 0:
        raise ValueError("Healing cannot be negative")
    updated = _condition_map(conditions)
    if "medicine_applied" in updated:
        raise ValueError("Medicine has already succeeded for this injury episode")
    if not passed:
        return {"current_hp": current_hp, "conditions": list(updated.values())}
    dying = updated.get("dying")
    if dying is not None and not dying.get("stabilized"):
        raise ValueError("Dying investigators require successful First Aid before Medicine")
    updated.pop("dying", None)
    updated["medicine_applied"] = {
        "type": "medicine_applied",
        "active": True,
    }
    hp = min(max_hp, current_hp + healing)
    if hp * 2 >= max_hp:
        updated.pop("major_wound", None)
    if hp > 0:
        updated.pop("unconscious", None)
    return {"current_hp": hp, "conditions": list(updated.values())}


def apply_natural_healing(
    *,
    current_hp: int,
    max_hp: int,
    conditions: list[dict[str, Any]],
    period_id: str,
    days: int = 1,
    con_success_level: SuccessLevel | None = None,
    healing_rolls: list[int] | None = None,
) -> dict[str, Any]:
    """Apply one replayable daily or weekly CoC7 natural-healing period."""

    if max_hp <= 0 or not 0 <= current_hp <= max_hp:
        raise ValueError("Hit point state is invalid")
    normalized_period = period_id.strip()
    if not normalized_period:
        raise ValueError("Natural healing requires a calendar period identifier")
    if days < 1:
        raise ValueError("Natural healing days must be positive")
    updated = _condition_map(conditions)
    prior_period = updated.get("natural_healing_period")
    if prior_period and prior_period.get("period_id") == normalized_period:
        raise ValueError("This natural-healing period was already resolved")
    has_major_wound = "major_wound" in updated
    if not has_major_wound:
        if con_success_level is not None or healing_rolls:
            raise ValueError("Ordinary daily healing does not take a CON roll")
        recovered = days
        cadence = "daily"
    else:
        recovered = _major_wound_recovery(
            con_success_level, list(healing_rolls or [])
        )
        cadence = "weekly"
    hp = min(max_hp, current_hp + recovered)
    major_wound_cleared = has_major_wound and (
        con_success_level in {"extreme", "critical"} or hp * 2 >= max_hp
    )
    if major_wound_cleared:
        updated.pop("major_wound", None)
    if hp > 0:
        updated.pop("unconscious", None)
    updated["natural_healing_period"] = {
        "type": "natural_healing_period",
        "active": True,
        "period_id": normalized_period,
        "cadence": cadence,
    }
    return {
        "current_hp": hp,
        "recovered": recovered,
        "cadence": cadence,
        "major_wound_cleared": major_wound_cleared,
        "conditions": list(updated.values()),
    }


def _major_wound_recovery(
    con_success_level: SuccessLevel | None, rolls: list[int]
) -> int:
    if con_success_level is None:
        raise ValueError("A major wound requires a weekly CON healing result")
    if con_success_level in {"failure", "fumble"}:
        if rolls:
            raise ValueError("A failed weekly healing roll restores no hit points")
        return 0
    expected = 2 if con_success_level in {"extreme", "critical"} else 1
    if len(rolls) != expected or any(
        type(roll) is not int or not 1 <= roll <= 3 for roll in rolls
    ):
        raise ValueError(f"Weekly healing requires {expected} recorded D3 roll(s)")
    return sum(rolls)


def apply_sanity_loss(
    *,
    current_san: int,
    maximum_san: int,
    intelligence: int,
    roll: int,
    success_loss: int,
    failure_loss: int,
    daily_loss_before: int,
    daily_starting_san: int,
    intelligence_roll: int | None = None,
    bout_roll: int | None = None,
    bout_duration: int | None = None,
) -> dict[str, Any]:
    """Resolve SAN loss and the temporary/indefinite insanity thresholds."""

    if not 0 <= current_san <= maximum_san <= 99:
        raise ValueError("Sanity state is invalid")
    if not 1 <= roll <= 100:
        raise ValueError("Sanity roll must be between 1 and 100")
    if success_loss < 0 or failure_loss < 0:
        raise ValueError("Sanity loss cannot be negative")
    if daily_loss_before < 0 or not 0 <= daily_starting_san <= 99:
        raise ValueError("Daily sanity baseline is invalid")
    passed = roll <= current_san
    loss = success_loss if passed else failure_loss
    new_san = max(0, current_san - loss)
    daily_loss = daily_loss_before + (current_san - new_san)
    temporary = False
    requires_intelligence_roll = loss >= 5 and intelligence_roll is None
    if intelligence_roll is not None:
        if not 1 <= intelligence_roll <= 100:
            raise ValueError("Intelligence roll must be between 1 and 100")
        temporary = loss >= 5 and intelligence_roll <= intelligence
    indefinite = daily_starting_san > 0 and daily_loss * 5 >= daily_starting_san
    bout = None
    if (temporary or indefinite) and bout_roll is not None:
        if not 1 <= bout_roll <= 10 or bout_duration is None or not 1 <= bout_duration <= 10:
            raise ValueError("Bout roll and duration must both be between 1 and 10")
        bout = {
            "type": "bout_of_madness",
            "active": True,
            "table_roll": bout_roll,
            "duration": bout_duration,
            "duration_unit": "round",
        }
    return {
        "passed": passed,
        "loss": current_san - new_san,
        "current_san": new_san,
        "daily_san_loss": daily_loss,
        "temporary_insanity": temporary,
        "indefinite_insanity": indefinite,
        "permanent_insanity": new_san == 0,
        "requires_intelligence_roll": requires_intelligence_roll,
        "requires_bout": (temporary or indefinite) and bout is None,
        "bout": bout,
    }


def adjusted_chase_move(base_move: int, con_success_level: SuccessLevel) -> int:
    if base_move < 0:
        raise ValueError("MOV cannot be negative")
    if con_success_level in {"extreme", "critical"}:
        return base_move + 1
    if con_success_level in {"failure", "fumble"}:
        return max(0, base_move - 1)
    return base_move


def chase_action_points(adjusted_move: int, slowest_move: int) -> int:
    if adjusted_move < 0 or slowest_move < 0:
        raise ValueError("Adjusted MOV cannot be negative")
    return adjusted_move - slowest_move + 1


def resolve_chase_hazard(
    *,
    action_points: int,
    passed: bool,
    failure_action_cost: int,
    damage: int = 0,
) -> dict[str, int | bool]:
    if action_points < 1:
        raise ValueError("At least one action point is required to attempt a hazard")
    if failure_action_cost < 0 or damage < 0:
        raise ValueError("Hazard costs cannot be negative")
    spent = 1 if passed else 1 + failure_action_cost
    return {
        "passed": passed,
        "action_points_spent": min(action_points, spent),
        "action_points_remaining": max(0, action_points - spent),
        "damage": 0 if passed else damage,
    }


def resolve_skill_development(
    *, current_value: int, development_roll: int, increase_roll: int | None
) -> dict[str, int | bool]:
    if not 0 <= current_value <= 200 or not 1 <= development_roll <= 100:
        raise ValueError("Development values are invalid")
    improved = development_roll > current_value or development_roll > 95
    if improved:
        if increase_roll is None or not 1 <= increase_roll <= 10:
            raise ValueError("An improving skill requires a recorded 1D10 result")
        increase = increase_roll
    else:
        if increase_roll is not None:
            raise ValueError("A non-improving skill must not include an increase roll")
        increase = 0
    return {
        "improved": improved,
        "old_value": current_value,
        "development_roll": development_roll,
        "increase": increase,
        "new_value": current_value + increase,
        "growth_mark_cleared": True,
    }


def _condition_map(
    conditions: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in deepcopy(conditions or []):
        if not isinstance(raw, dict):
            raise TypeError("Conditions must be objects")
        condition_type = str(raw.get("type") or "").strip()
        if not condition_type:
            raise ValueError("Every condition requires a type")
        if raw.get("active", True):
            raw["type"] = condition_type
            raw["active"] = True
            identity = condition_type
            if condition_type in {"skill_growth_mark", "development_pending"}:
                identity = ":".join(
                    (
                        condition_type,
                        str(raw.get("skill_key") or ""),
                        str(raw.get("specialization") or ""),
                    )
                )
            result[identity] = raw
    return result


__all__ = [
    "adjusted_chase_move",
    "apply_damage",
    "apply_first_aid",
    "apply_medicine",
    "apply_natural_healing",
    "apply_sanity_loss",
    "chase_action_points",
    "evaluate_recorded_dice",
    "resolve_chase_hazard",
    "resolve_dying_con",
    "resolve_fighting_maneuver",
    "resolve_major_wound_con",
    "resolve_melee_exchange",
    "resolve_skill_development",
    "validate_dice_expression",
]
