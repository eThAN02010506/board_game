import pytest

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
    resolve_melee_exchange,
    resolve_skill_development,
)


def test_melee_ties_favor_dodge_but_initiating_attack_over_fight_back() -> None:
    dodged = resolve_melee_exchange(
        attacker_target=60,
        attacker_roll=40,
        defender_target=50,
        defender_roll=30,
        defense="dodge",
    )
    fought = resolve_melee_exchange(
        attacker_target=60,
        attacker_roll=40,
        defender_target=50,
        defender_roll=30,
        defense="fight_back",
    )
    assert dodged["outcome"] == "defender_avoids"
    assert fought["outcome"] == "attacker_hits"
    assert not fought["push_allowed"]


def test_failed_melee_rolls_never_inflict_damage() -> None:
    result = resolve_melee_exchange(
        attacker_target=40,
        attacker_roll=80,
        defender_target=40,
        defender_roll=70,
        defense="fight_back",
    )
    assert result["outcome"] == "miss"


def test_fighting_maneuver_enforces_build_difference() -> None:
    possible = resolve_fighting_maneuver(
        attacker_target=60,
        attacker_roll=20,
        attacker_build=0,
        defender_target=50,
        defender_roll=60,
        defender_build=2,
        defense="fight_back",
    )
    assert possible["maneuver_succeeds"]
    assert possible["penalty_dice"] == 2
    impossible = resolve_fighting_maneuver(
        attacker_target=80,
        attacker_roll=1,
        attacker_build=0,
        defender_target=20,
        defender_roll=100,
        defender_build=3,
        defense="dodge",
    )
    assert impossible["outcome"] == "ineffective"
    assert not impossible["maneuver_succeeds"]


def test_damage_major_wound_dying_first_aid_and_medicine_flow() -> None:
    damaged = apply_damage(current_hp=10, max_hp=10, damage=6)
    assert damaged["current_hp"] == 4
    assert damaged["major_wound"]
    assert damaged["requires_major_wound_con_check"]

    dying = apply_damage(
        current_hp=4,
        max_hp=10,
        damage=4,
        conditions=damaged["conditions"],
    )
    assert dying["current_hp"] == 0
    assert dying["requires_dying_con_check"]

    stabilized = apply_first_aid(
        current_hp=0,
        max_hp=10,
        conditions=dying["conditions"],
        passed=True,
    )
    assert stabilized["current_hp"] == 1
    assert next(
        item for item in stabilized["conditions"] if item["type"] == "dying"
    )["stabilized"]

    treated = apply_medicine(
        current_hp=1,
        max_hp=10,
        conditions=stabilized["conditions"],
        passed=True,
        healing=3,
    )
    assert treated["current_hp"] == 4
    assert all(item["type"] != "dying" for item in treated["conditions"])
    assert any(item["type"] == "major_wound" for item in treated["conditions"])


def test_massive_damage_and_failed_dying_check_are_terminal() -> None:
    massive = apply_damage(current_hp=10, max_hp=10, damage=10)
    assert massive["instant_death"]
    assert any(item["type"] == "dead" for item in massive["conditions"])

    dying = apply_damage(current_hp=6, max_hp=10, damage=6)
    result = resolve_dying_con(dying["conditions"], passed=False)
    assert any(item["type"] == "dead" for item in result)
    assert all(item["type"] != "dying" for item in result)


def test_treatment_is_once_per_injury_and_natural_healing_uses_cadence() -> None:
    wounded = apply_damage(current_hp=10, max_hp=10, damage=6)
    aided = apply_first_aid(
        current_hp=4,
        max_hp=10,
        conditions=wounded["conditions"],
        passed=True,
    )
    with pytest.raises(ValueError, match="already succeeded"):
        apply_first_aid(
            current_hp=5,
            max_hp=10,
            conditions=aided["conditions"],
            passed=True,
        )
    weekly = apply_natural_healing(
        current_hp=5,
        max_hp=10,
        conditions=aided["conditions"],
        period_id="1923-W08",
        con_success_level="extreme",
        healing_rolls=[2, 3],
    )
    assert weekly["current_hp"] == 10
    assert weekly["major_wound_cleared"]
    with pytest.raises(ValueError, match="already resolved"):
        apply_natural_healing(
            current_hp=10,
            max_hp=10,
            conditions=weekly["conditions"],
            period_id="1923-W08",
        )

    newly_harmed = apply_damage(
        current_hp=10,
        max_hp=10,
        damage=1,
        conditions=weekly["conditions"],
    )
    assert all(
        item["type"] != "first_aid_applied"
        for item in newly_harmed["conditions"]
    )
    daily = apply_natural_healing(
        current_hp=9,
        max_hp=10,
        conditions=newly_harmed["conditions"],
        period_id="1923-02-27",
    )
    assert daily["current_hp"] == 10
    assert daily["cadence"] == "daily"


def test_sanity_thresholds_produce_replayable_follow_up_requirements() -> None:
    pending = apply_sanity_loss(
        current_san=60,
        maximum_san=99,
        intelligence=70,
        roll=80,
        success_loss=0,
        failure_loss=5,
        daily_loss_before=0,
        daily_starting_san=60,
    )
    assert pending["current_san"] == 55
    assert pending["requires_intelligence_roll"]

    resolved = apply_sanity_loss(
        current_san=60,
        maximum_san=99,
        intelligence=70,
        roll=80,
        success_loss=0,
        failure_loss=12,
        daily_loss_before=0,
        daily_starting_san=60,
        intelligence_roll=40,
        bout_roll=7,
        bout_duration=4,
    )
    assert resolved["temporary_insanity"]
    assert resolved["indefinite_insanity"]
    assert resolved["bout"]["table_roll"] == 7
    destroyed = apply_sanity_loss(
        current_san=4,
        maximum_san=99,
        intelligence=70,
        roll=90,
        success_loss=0,
        failure_loss=8,
        daily_loss_before=0,
        daily_starting_san=4,
        intelligence_roll=40,
        bout_roll=1,
        bout_duration=1,
    )
    assert destroyed["current_san"] == 0
    assert destroyed["permanent_insanity"]


def test_chase_and_development_rules_are_deterministic() -> None:
    assert adjusted_chase_move(8, "extreme") == 9
    assert adjusted_chase_move(8, "failure") == 7
    assert chase_action_points(9, 7) == 3
    hazard = resolve_chase_hazard(
        action_points=3,
        passed=False,
        failure_action_cost=2,
        damage=4,
    )
    assert hazard == {
        "passed": False,
        "action_points_spent": 3,
        "action_points_remaining": 0,
        "damage": 4,
    }
    growth = resolve_skill_development(
        current_value=55,
        development_roll=68,
        increase_roll=8,
    )
    assert growth["new_value"] == 63
    assert growth["growth_mark_cleared"]


def test_recorded_dice_rejects_unreplayable_or_out_of_range_values() -> None:
    assert evaluate_recorded_dice("2d6+3", [2, 5]) == 10
    assert evaluate_recorded_dice("4", []) == 4
    with pytest.raises(ValueError):
        evaluate_recorded_dice("1d6", [7])
