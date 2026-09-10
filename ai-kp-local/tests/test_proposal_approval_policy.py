import unittest

from ai_kp.application.play.proposal_approval import plan_proposed_checks
from ai_kp.platform.resolution.proposals import validate_unresolved_check_boundary
from ai_kp.rulesets import get_ruleset


class ProposalApprovalPolicyTests(unittest.TestCase):
    def test_check_plan_pins_ruleset_validation_and_actor(self) -> None:
        proposal = {
            "pc_id": "pc-default",
            "proposed_checks": [
                {
                    "skill": "侦查",
                    "difficulty": "hard",
                    "hidden": True,
                }
            ],
        }

        plans = plan_proposed_checks(
            proposal,
            get_ruleset("coc7-keeper-cn-2002c"),
        )

        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].skill_name, "侦查")
        self.assertEqual(plans[0].difficulty, "hard")
        self.assertTrue(plans[0].hidden)
        self.assertFalse(plans[0].allow_push)
        self.assertEqual(plans[0].pc_id, "pc-default")

    def test_invalid_difficulty_is_rejected_before_persistence(self) -> None:
        proposal = {
            "pc_id": None,
            "proposed_checks": [
                {
                    "skill": "侦查",
                    "difficulty": "impossible",
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "difficulty"):
            plan_proposed_checks(
                proposal,
                get_ruleset("coc7-keeper-cn-2002c"),
            )

    def test_unresolved_check_cannot_carry_any_world_effect_kind(self) -> None:
        effect_groups = [
            ([{"summary": "event"}], [], [], []),
            ([], [{"text": "memory"}], [], []),
            ([], [], [{"npc_id": "npc"}], []),
            ([], [], [], [{"token_id": "token"}]),
        ]
        for events, memories, npc_updates, map_moves in effect_groups:
            with self.subTest(effect_groups=(events, memories, npc_updates, map_moves)):
                with self.assertRaisesRegex(ValueError, "unresolved checks"):
                    validate_unresolved_check_boundary(
                        [{"skill": "侦查"}],
                        events,
                        memories,
                        npc_updates,
                        map_moves,
                    )


if __name__ == "__main__":
    unittest.main()
