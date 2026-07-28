import unittest

from ai_kp.rulesets.coc7.mechanics.opposed_check import (
    OpposedParticipant,
    resolve_opposed,
)
from ai_kp.rulesets.coc7.mechanics.skill_check import resolve_d100
from ai_kp.rulesets.registry import get_ruleset


class OpposedCheckRuleTests(unittest.TestCase):
    def test_higher_success_level_wins(self) -> None:
        left = OpposedParticipant("investigator", target=60, roll=20)
        right = OpposedParticipant("cultist", target=80, roll=60)

        result = resolve_opposed(left, right)

        self.assertEqual(left.success_level, "hard")
        self.assertEqual(right.success_level, "regular")
        self.assertEqual(result.outcome, "left_wins")
        self.assertEqual(result.winner_id, "investigator")
        self.assertEqual(result.loser_id, "cultist")
        self.assertEqual(result.decided_by, "success_level")

    def test_same_success_level_uses_higher_skill_or_attribute(self) -> None:
        left = OpposedParticipant("felix", target=65, roll=51)
        right = OpposedParticipant("harrison", target=55, roll=41)

        result = resolve_opposed(left, right)

        self.assertEqual(left.success_level, "regular")
        self.assertEqual(right.success_level, "regular")
        self.assertEqual(result.outcome, "left_wins")
        self.assertEqual(result.winner_id, "felix")
        self.assertEqual(result.decided_by, "target")

    def test_equal_level_and_target_leave_keeper_choice_explicit(self) -> None:
        left = OpposedParticipant("left", target=50, roll=30)
        right = OpposedParticipant("right", target=50, roll=40)

        result = resolve_opposed(left, right)
        serialized = result.as_dict()

        self.assertEqual(result.outcome, "tie")
        self.assertIsNone(result.winner_id)
        self.assertIsNone(result.loser_id)
        self.assertEqual(result.decided_by, "unresolved_tie")
        self.assertEqual(result.tie_options, ("stalemate", "reroll"))
        self.assertEqual(serialized["tie_options"], ["stalemate", "reroll"])
        self.assertFalse(serialized["push_allowed"])
        self.assertFalse(serialized["difficulty_used"])

    def test_failure_and_fumble_remain_distinct_success_levels(self) -> None:
        left = OpposedParticipant("failed", target=60, roll=90)
        right = OpposedParticipant("fumbled", target=40, roll=96)

        result = resolve_opposed(left, right)

        self.assertEqual(left.success_level, "failure")
        self.assertEqual(right.success_level, "fumble")
        self.assertEqual(result.outcome, "left_wins")
        self.assertEqual(result.decided_by, "success_level")

    def test_bonus_dice_are_selected_before_opposed_comparison(self) -> None:
        left_roll = resolve_d100(
            target=55,
            difficulty="regular",
            bonus_dice=1,
            ones_digit=4,
            tens_digits=(4, 2),
        )
        right_roll = resolve_d100(
            target=55,
            difficulty="regular",
            bonus_dice=0,
            ones_digit=5,
            tens_digits=(4,),
        )

        result = resolve_opposed(
            OpposedParticipant.from_d100_resolution("malcolm", left_roll),
            OpposedParticipant.from_d100_resolution("hugh", right_roll),
        )

        self.assertEqual(result.left.roll, 24)
        self.assertEqual(result.left.success_level, "hard")
        self.assertEqual(result.right.roll, 45)
        self.assertEqual(result.right.success_level, "regular")
        self.assertEqual(result.winner_id, "malcolm")

    def test_serialized_result_is_deterministically_replayable(self) -> None:
        left = OpposedParticipant("caster", target=70, roll=14)
        right = OpposedParticipant("target", target=80, roll=16)

        first = resolve_opposed(left, right).as_dict()
        replayed = resolve_opposed(
            OpposedParticipant(
                first["left"]["participant_id"],
                first["left"]["target"],
                first["left"]["roll"],
            ),
            OpposedParticipant(
                first["right"]["participant_id"],
                first["right"]["target"],
                first["right"]["roll"],
            ),
        ).as_dict()

        self.assertEqual(first, replayed)
        self.assertEqual(first["winner_id"], "target")
        self.assertEqual(first["decided_by"], "target")
        self.assertEqual(first["source_reference"]["sections"][0], "5.7 对抗检定")

    def test_serialized_source_reference_cannot_pollute_future_replays(self) -> None:
        left = OpposedParticipant("left", target=50, roll=20)
        right = OpposedParticipant("right", target=40, roll=30)

        first = resolve_opposed(left, right).as_dict()
        first["source_reference"]["sections"].append("caller mutation")
        replayed = resolve_opposed(left, right).as_dict()

        self.assertNotIn("caller mutation", replayed["source_reference"]["sections"])

    def test_invalid_participants_and_rolls_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be blank"):
            OpposedParticipant(" ", target=50, roll=20)
        with self.assertRaisesRegex(ValueError, "cannot be blank"):
            OpposedParticipant(None, target=50, roll=20)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            OpposedParticipant("left", target=101, roll=20)
        with self.assertRaisesRegex(ValueError, "between 1 and 100"):
            OpposedParticipant("left", target=50, roll=0)
        with self.assertRaisesRegex(ValueError, "target must be an integer"):
            OpposedParticipant("left", target=True, roll=20)
        with self.assertRaisesRegex(ValueError, "roll must be an integer"):
            OpposedParticipant("left", target=50, roll=False)
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            resolve_opposed(
                OpposedParticipant("same", target=50, roll=20),
                OpposedParticipant("same", target=60, roll=30),
            )

    def test_installed_ruleset_exposes_the_replayable_opposed_port(self) -> None:
        ruleset = get_ruleset("coc7")

        result = ruleset.resolve_opposed_check(
            left_participant_id="investigator",
            left_target=70,
            left_roll=50,
            right_participant_id="cultist",
            right_target=40,
            right_roll=30,
        )

        self.assertIn("opposed_check", ruleset.manifest.capabilities)
        self.assertEqual(result["winner_id"], "investigator")
        self.assertEqual(result["decided_by"], "target")
        self.assertFalse(result["push_allowed"])
        self.assertFalse(result["difficulty_used"])


if __name__ == "__main__":
    unittest.main()
