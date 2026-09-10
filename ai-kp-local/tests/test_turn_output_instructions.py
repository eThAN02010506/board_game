import json
import unittest

from ai_kp.director.turn_output import (
    STRUCTURED_OUTPUT_INSTRUCTIONS,
    StructuredOutputError,
    parse_kp_turn_output,
)


class TurnOutputInstructionTests(unittest.TestCase):
    def test_checks_belong_to_the_actor_performing_the_uncertain_action(self) -> None:
        self.assertIn("检定必须归属于实际执行不确定行动的一方", STRUCTURED_OUTPUT_INSTRUCTIONS)
        self.assertIn("不得让玩家用自己的", STRUCTURED_OUTPUT_INSTRUCTIONS)
        self.assertIn("不得把它倒置成玩家替目标检定听见与否", STRUCTURED_OUTPUT_INSTRUCTIONS)

    def test_repairs_feasibility_value_copied_into_resolution_slot(self) -> None:
        payload = {
            "public_narration": "对方听完了你的问题。",
            "kp_notes": "",
            "action_ruling": {
                "goal": "了解委托",
                "method": "普通交谈",
                "target": "委托人",
                "feasibility": "partial",
                "resolution": "partial",
                "reason": "可以直接交谈",
                "maximum_effect": "获得对方愿意公开的信息",
                "forbidden_outcome_claims": [],
                "alternative": "",
            },
            "proposed_checks": [],
            "proposed_events": [],
            "proposed_memories": [],
            "proposed_npc_updates": [],
            "proposed_map_moves": [],
            "proposed_facts": [],
        }

        output = parse_kp_turn_output(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(output.action_ruling.resolution, "automatic")

    def test_repairs_feasible_no_roll_as_automatic(self) -> None:
        payload = {
            "public_narration": "维托里奥继续说了下去。",
            "kp_notes": "",
            "action_ruling": {
                "goal": "听取已愿意回答者的原话",
                "method": "耐心等待",
                "target": "维托里奥",
                "feasibility": "possible",
                "resolution": "no_roll",
                "reason": "对方已同意回答，无需掷骰",
                "maximum_effect": "听到对方愿意透露的内容",
                "forbidden_outcome_claims": [],
                "alternative": "",
            },
            "proposed_checks": [],
            "proposed_events": [],
            "proposed_memories": [],
            "proposed_npc_updates": [],
            "proposed_map_moves": [],
            "proposed_facts": [],
        }

        output = parse_kp_turn_output(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(output.action_ruling.resolution, "automatic")

    def test_does_not_guess_unrelated_unknown_resolution(self) -> None:
        payload = {
            "public_narration": "尚未结算。",
            "kp_notes": "",
            "action_ruling": {
                "goal": "调查",
                "method": "查看",
                "target": "现场",
                "feasibility": "possible",
                "resolution": "maybe",
                "reason": "未知",
                "maximum_effect": "只能看到现场线索",
                "forbidden_outcome_claims": [],
                "alternative": "",
            },
            "proposed_checks": [],
            "proposed_events": [],
            "proposed_memories": [],
            "proposed_npc_updates": [],
            "proposed_map_moves": [],
            "proposed_facts": [],
        }

        with self.assertRaises(StructuredOutputError):
            parse_kp_turn_output(json.dumps(payload, ensure_ascii=False))

    def test_joins_pure_string_lists_in_scalar_prose_slots(self) -> None:
        payload = {
            "public_narration": ["维托里奥翻开圣经", "他说出一句警告"],
            "kp_notes": "",
            "action_ruling": {
                "goal": ["观察圣经", "记下原话"],
                "method": "耐心聆听",
                "target": "维托里奥",
                "feasibility": "possible",
                "resolution": "automatic",
                "reason": "对方已愿意回答",
                "maximum_effect": "得到他愿意说出的线索",
                "forbidden_outcome_claims": [],
                "alternative": "",
            },
            "proposed_checks": [],
            "proposed_events": [],
            "proposed_memories": [],
            "proposed_npc_updates": [],
            "proposed_map_moves": [],
            "proposed_facts": [],
        }

        output = parse_kp_turn_output(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(output.action_ruling.goal, "观察圣经；记下原话")
        self.assertEqual(
            output.public_narration, "维托里奥翻开圣经；他说出一句警告"
        )


if __name__ == "__main__":
    unittest.main()
