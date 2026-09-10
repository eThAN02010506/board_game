import json
import unittest

from ai_kp.director.check_consequence import (
    check_consequence_output_instructions,
    enforce_effect_ceiling_consistency,
    enforce_failure_consistency,
    enforce_narrative_quality,
    parse_check_consequence_output,
)
from ai_kp.director.turn_output import StructuredOutputError
from ai_kp.platform.resolution import HIDDEN_CHECK_PUBLIC_NARRATION


def payload() -> dict:
    return {
        "public_narration": "你在档案柜后发现了一张撕碎的收据。",
        "kp_notes": "检定结果已验证。",
        "action_ruling": {
            "goal": "找到档案柜附近的隐藏线索",
            "method": "已经完成的侦查检定",
            "target": "档案柜",
            "feasibility": "possible",
            "resolution": "automatic",
            "reason": "已验证检定结果支持发现线索。",
            "maximum_effect": "发现一张撕碎的收据。",
            "alternative": "",
        },
        "proposed_checks": [],
        "proposed_events": [
            {
                "event_type": "clue_found",
                "summary": "调查员发现撕碎的收据。",
                "actor_type": "pc",
                "actor_id": None,
                "visibility": "table",
                "happened_at": None,
                "payload": {},
            }
        ],
        "proposed_memories": [],
        "proposed_npc_updates": [],
        "proposed_map_moves": [],
    }


class CheckConsequenceOutputTests(unittest.TestCase):
    def test_accepts_effects_after_a_resolved_check(self) -> None:
        output = parse_check_consequence_output(
            json.dumps(payload(), ensure_ascii=False)
        )
        self.assertEqual(output.proposed_events[0].event_type, "clue_found")

    def test_rejects_new_checks_and_map_moves(self) -> None:
        with_check = payload()
        with_check["proposed_checks"] = [
            {
                "skill": "侦查",
                "difficulty": "regular",
                "reason": "roll forever",
                "pc_id": None,
                "hidden": False,
            }
        ]
        with self.assertRaises(StructuredOutputError):
            parse_check_consequence_output(json.dumps(with_check))

        with_move = payload()
        with_move["proposed_map_moves"] = [
            {
                "token_id": "token-1",
                "to_location_name": "vault",
                "reason": "not in the first slice",
                "require_route": True,
            }
        ]
        with self.assertRaisesRegex(StructuredOutputError, "cannot move map"):
            parse_check_consequence_output(json.dumps(with_move))

    def test_hidden_batch_redacts_public_text_but_preserves_kp_only_details(
        self,
    ) -> None:
        hidden_payload = payload()
        hidden_payload["public_narration"] = (
            "暗骰 D100=04，是极难成功，所以你立刻发现了密门。"
        )
        hidden_payload["kp_notes"] = "暗骰 04，极难成功；密门线索暂不公开。"
        hidden_payload["proposed_events"][0]["visibility"] = "kp"
        hidden_payload["proposed_events"][0]["summary"] = "暗骰 04 发现密门。"
        hidden_payload["proposed_memories"] = [
            {
                "text": "暗骰 04 发现密门。",
                "scope": "clue",
                "importance": 3,
                "visibility": "kp",
                "pc_id": None,
                "npc_id": None,
                "happened_at": None,
            }
        ]

        output = parse_check_consequence_output(
            json.dumps(hidden_payload, ensure_ascii=False),
            hidden_batch=True,
        )

        self.assertEqual(
            output.public_narration,
            HIDDEN_CHECK_PUBLIC_NARRATION,
        )
        self.assertNotIn("04", output.public_narration)
        self.assertIn("04", output.kp_notes)
        self.assertTrue(
            all(event.visibility == "kp" for event in output.proposed_events)
        )
        self.assertTrue(
            all(memory.visibility == "kp" for memory in output.proposed_memories)
        )

    def test_hidden_batch_rejects_non_kp_effects_and_npc_updates(self) -> None:
        leaking_event = payload()
        leaking_event["proposed_events"][0]["visibility"] = "player"
        leaking_memory = payload()
        leaking_memory["proposed_events"] = []
        leaking_memory["proposed_memories"] = [
            {
                "text": "暗骰大成功。",
                "scope": "campaign_fact",
                "importance": 1,
                "visibility": "table",
                "pc_id": None,
                "npc_id": None,
                "happened_at": None,
            }
        ]
        leaking_npc = payload()
        leaking_npc["proposed_events"] = []
        leaking_npc["proposed_npc_updates"] = [
            {
                "npc_id": "npc-secret",
                "appeared": False,
                "relationship_delta": 2,
                "last_seen_time": None,
                "note": "因暗骰结果改变关系。",
            }
        ]
        leaking_fact = payload()
        leaking_fact["proposed_events"] = []
        leaking_fact["proposed_facts"] = [
            {
                "fact_type": "canonical_fact",
                "subject": "密门",
                "predicate": "存在",
                "object_text": "暗骰已发现",
                "pc_id": None,
                "evidence_event_ids": [],
                "happened_at": None,
            }
        ]

        cases = (
            (leaking_event, "KP-visible events"),
            (leaking_memory, "KP-visible memories"),
            (leaking_npc, "cannot propose NPC updates"),
            (leaking_fact, "KP-visible facts"),
        )
        for candidate, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(StructuredOutputError, message):
                    parse_check_consequence_output(
                        json.dumps(candidate, ensure_ascii=False),
                        hidden_batch=True,
                    )

    def test_hidden_prompt_requires_the_fixed_public_placeholder(self) -> None:
        instructions = check_consequence_output_instructions(hidden_batch=True)

        self.assertIn(HIDDEN_CHECK_PUBLIC_NARRATION, instructions)
        self.assertIn('visibility 必须全部为 "kp"', instructions)
        self.assertIn("proposed_npc_updates 必须为空", instructions)

    def test_failed_check_rejects_success_hidden_in_structured_effects(self) -> None:
        candidate = payload()
        candidate["public_narration"] = "你没有从表面看出任何异常。"
        output = parse_check_consequence_output(
            json.dumps(candidate, ensure_ascii=False)
        )

        with self.assertRaisesRegex(StructuredOutputError, "cannot claim or persist"):
            enforce_failure_consistency(output, all_checks_passed=False)

    def test_failed_check_allows_explicit_failure_consequences(self) -> None:
        candidate = payload()
        candidate["public_narration"] = "你没有找到收据，翻动文件的声音惊动了守卫。"
        candidate["proposed_events"] = [
            {
                "event_type": "guard_alerted",
                "summary": "搜索失败惊动了守卫。",
                "actor_type": "environment",
                "actor_id": None,
                "visibility": "table",
                "happened_at": None,
                "payload": {},
            }
        ]
        output = parse_check_consequence_output(
            json.dumps(candidate, ensure_ascii=False)
        )

        self.assertIs(
            enforce_failure_consistency(output, all_checks_passed=False),
            output,
        )

    def test_failed_social_check_rejects_implicit_partial_success(self) -> None:
        candidate = payload()
        candidate["public_narration"] = (
            "你未能说服列车长交出钥匙，但他暂时相信了你的亲属说法，愿意继续让步。"
        )
        candidate["proposed_events"] = []
        candidate["proposed_memories"] = []
        output = parse_check_consequence_output(
            json.dumps(candidate, ensure_ascii=False)
        )

        with self.assertRaisesRegex(StructuredOutputError, "cannot claim or persist"):
            enforce_failure_consistency(output, all_checks_passed=False)

    def test_dangerous_jump_ceiling_rejects_safe_landing_even_on_success(self) -> None:
        candidate = payload()
        candidate["public_narration"] = "你从高速列车跳下，却毫发无伤地安全落地。"
        candidate["proposed_events"] = []
        candidate["proposed_memories"] = []
        output = parse_check_consequence_output(
            json.dumps(candidate, ensure_ascii=False)
        )

        with self.assertRaisesRegex(StructuredOutputError, "effect constraint"):
            enforce_effect_ceiling_consistency(
                output,
                forbidden_outcome_claims=("毫发无伤", "安全落地"),
            )

    def test_rejects_weak_model_clause_loops(self) -> None:
        candidate = payload()
        candidate["public_narration"] = (
            "你撞向车门，车门被撞到，车门扉跳下，车门被撞到，"
            "你跌落，车门扉跳下，车门被撞到，车门扉跳下，"
            "你跌落，车门被撞到，车门扉跳下，车门被撞到。"
        )
        candidate["proposed_events"] = []
        candidate["proposed_memories"] = []
        output = parse_check_consequence_output(
            json.dumps(candidate, ensure_ascii=False)
        )

        with self.assertRaisesRegex(StructuredOutputError, "repeated clauses"):
            enforce_narrative_quality(output)

    def test_failed_check_rejects_success_event_type_and_payload_state(self) -> None:
        for event_type, event_payload in (
            ("door_opened", {}),
            ("environment_changed", {"door": {"state": "opened"}}),
            ("environment_changed", {"clue": {"discovered": True}}),
        ):
            with self.subTest(event_type=event_type, payload=event_payload):
                candidate = payload()
                candidate["public_narration"] = "你没能打开驾驶室的门。"
                candidate["proposed_events"] = [
                    {
                        "event_type": event_type,
                        "summary": "门锁和周围环境保持原状。",
                        "actor_type": "environment",
                        "actor_id": None,
                        "visibility": "table",
                        "happened_at": None,
                        "payload": event_payload,
                    }
                ]
                output = parse_check_consequence_output(
                    json.dumps(candidate, ensure_ascii=False)
                )

                with self.assertRaisesRegex(
                    StructuredOutputError,
                    "event type or state transition",
                ):
                    enforce_failure_consistency(output, all_checks_passed=False)

    def test_failed_check_allows_adverse_found_event(self) -> None:
        candidate = payload()
        candidate["public_narration"] = "你没找到出口，反而和巡逻守卫撞个正着。"
        candidate["proposed_events"] = [
            {
                "event_type": "guard_found_player",
                "summary": "守卫撞见了正在撬锁的调查员。",
                "actor_type": "npc",
                "actor_id": None,
                "visibility": "table",
                "happened_at": None,
                "payload": {"guard_alerted": True},
            }
        ]
        output = parse_check_consequence_output(
            json.dumps(candidate, ensure_ascii=False)
        )

        self.assertIs(
            enforce_failure_consistency(output, all_checks_passed=False),
            output,
        )


if __name__ == "__main__":
    unittest.main()
