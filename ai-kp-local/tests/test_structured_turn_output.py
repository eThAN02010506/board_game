import json
import tempfile
import unittest
from pathlib import Path

from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.kp.orchestrator import KpOrchestrator
from ai_kp.kp.turn_output import StructuredOutputError, parse_kp_turn_output
from ai_kp.maps.generation import generate_map


def valid_output() -> dict:
    return {
        "public_narration": "门上的新鲜刮痕通向仓库内部。",
        "kp_notes": "刮痕来自被拖动的重物。",
        "action_ruling": {
            "goal": "检查仓库门",
            "method": "近距离观察",
            "target": "仓库门",
            "feasibility": "possible",
            "resolution": "automatic",
            "reason": "角色就在门前，普通观察不需要检定。",
            "maximum_effect": "看见门上无需专业能力即可发现的痕迹。",
            "alternative": "",
        },
        "proposed_checks": [],
        "proposed_events": [],
        "proposed_memories": [],
        "proposed_npc_updates": [],
        "proposed_map_moves": [],
    }


class FakeLlm:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls.append((messages, temperature))
        return self.responses.pop(0)


class StructuredTurnOutputTests(unittest.IsolatedAsyncioTestCase):
    def test_parser_rejects_unknown_or_missing_fields(self) -> None:
        payload = valid_output()
        payload["unknown"] = True
        with self.assertRaises(StructuredOutputError):
            parse_kp_turn_output(json.dumps(payload, ensure_ascii=False))

    def test_parser_rejects_whitespace_only_required_text(self) -> None:
        for field, value in (
            ("public_narration", " \n\t "),
            (
                "proposed_checks",
                [
                    {
                        "skill": "　",
                        "difficulty": "regular",
                        "reason": "需要检定",
                    }
                ],
            ),
        ):
            with self.subTest(field=field):
                payload = valid_output()
                payload[field] = value

                with self.assertRaises(StructuredOutputError):
                    parse_kp_turn_output(json.dumps(payload, ensure_ascii=False))

    def test_parser_trims_valid_structured_text(self) -> None:
        payload = valid_output()
        payload["public_narration"] = "  门后传来钟声。 \n"
        payload["kp_notes"] = "\t 仅 KP 可见。 "

        output = parse_kp_turn_output(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(output.public_narration, "门后传来钟声。")
        self.assertEqual(output.kp_notes, "仅 KP 可见。")

    def test_parser_rejects_world_effects_before_a_proposed_check_resolves(self) -> None:
        payload = valid_output()
        payload["action_ruling"]["resolution"] = "check"
        payload["proposed_checks"] = [
            {
                "skill": "侦查",
                "difficulty": "regular",
                "reason": "检查窗框",
            }
        ]
        payload["proposed_memories"] = [
            {
                "text": "窗框上有黑泥。",
                "scope": "clue",
                "importance": 2,
                "visibility": "table",
            }
        ]

        with self.assertRaisesRegex(StructuredOutputError, "unresolved checks"):
            parse_kp_turn_output(json.dumps(payload, ensure_ascii=False))

    def test_impossible_goal_cannot_roll_or_commit_effects(self) -> None:
        payload = valid_output()
        payload["action_ruling"] = {
            "goal": "把巨大口器吓得逃走",
            "method": "恐吓",
            "target": "7号车厢外的巨大口器",
            "feasibility": "impossible",
            "resolution": "no_roll",
            "reason": "目标没有可被社会技能胁迫的心智或退路。",
            "maximum_effect": "确认普通威胁无法迫使它退却。",
            "alternative": "重新声明为制造声音吸引注意，或立即撤回6号车厢。",
        }
        parse_kp_turn_output(json.dumps(payload, ensure_ascii=False))

        payload["proposed_events"] = [
            {
                "event_type": "monster_fled",
                "summary": "怪物被吓跑。",
                "actor_type": "npc",
                "visibility": "table",
            }
        ]
        with self.assertRaisesRegex(StructuredOutputError, "impossible action"):
            parse_kp_turn_output(json.dumps(payload, ensure_ascii=False))

    async def test_orchestrator_repairs_invalid_model_json_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "test.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                llm = FakeLlm(["这不是 JSON", json.dumps(valid_output(), ensure_ascii=False)])

                result = await KpOrchestrator(connection, llm).handle_player_action(
                    campaign_id=campaign["id"],
                    player_action="我检查仓库门。",
                )

                self.assertTrue(result.repaired)
                self.assertEqual(len(llm.calls), 2)
                self.assertIn("新鲜刮痕", result.output.public_narration)
                self.assertEqual(result.context.messages[-1]["role"], "user")
                self.assertEqual(
                    result.skill_ids,
                    (
                        "platform.turn_proposal",
                        "platform.module_scene_understanding",
                        "platform.npc_portrayal",
                        "platform.scene_direction",
                        "platform.output_safety_review",
                    ),
                )
                system_prompt = llm.calls[0][0][0].content
                self.assertIn("[AI Skill: platform.turn_proposal@1.0.0", system_prompt)
                self.assertIn(
                    "[AI Skill: platform.module_scene_understanding@1.0.0",
                    system_prompt,
                )
                self.assertIn("[AI Skill: platform.npc_portrayal@1.0.0", system_prompt)
                self.assertIn("[AI Skill: platform.scene_direction@1.0.0", system_prompt)
                self.assertIn("[AI Skill: platform.output_safety_review@1.0.0", system_prompt)

    async def test_orchestrator_reductively_repairs_repeated_precommitted_effects(
        self,
    ) -> None:
        payload = valid_output()
        payload["action_ruling"]["resolution"] = "check"
        payload["proposed_checks"] = [
            {"skill": "侦查", "difficulty": "regular", "reason": "需要仔细检查"}
        ]
        payload["proposed_events"] = [
            {
                "event_type": "clue_found",
                "summary": "检定前不应落地的线索。",
                "actor_type": "system",
                "visibility": "table",
            }
        ]
        repeated = json.dumps(payload, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "test.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("弱模型减法修复")
                llm = FakeLlm([repeated, repeated])

                result = await KpOrchestrator(connection, llm).handle_player_action(
                    campaign_id=campaign["id"],
                    player_action="我检查仓库门。",
                )

                self.assertTrue(result.repaired)
                self.assertEqual(len(result.output.proposed_checks), 1)
                self.assertEqual(result.output.proposed_events, [])
                self.assertEqual(len(llm.calls), 2)


class StructuredApprovalTests(unittest.TestCase):
    def test_approval_applies_all_validated_world_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "test.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                pc = repo.create_pc(campaign["id"], "林若川", {"侦查": 65})
                npc = repo.create_npc("周怀民", home_location="旧码头", profession="线人")
                repo.link_npc_to_campaign(campaign["id"], npc["id"], relationship_score=2)
                saved_map = repo.create_map(
                    campaign["id"],
                    generate_map(
                        "码头",
                        "旧码头、仓库",
                        location_names=["旧码头", "仓库"],
                        routes=[("旧码头", "仓库")],
                    ),
                )
                token = repo.place_map_token(
                    saved_map["id"], "林若川", "旧码头", actor_id=pc["id"]
                )
                proposal = repo.create_turn_proposal(
                    campaign_id=campaign["id"],
                    pc_id=pc["id"],
                    player_action="我跟周怀民去仓库检查。",
                    public_narration="你们到达仓库门口。",
                    kp_notes="隐藏的 KP 备注。",
                    proposed_events=[
                        {
                            "event_type": "clue_found",
                            "summary": "发现仓库门上的刮痕。",
                            "actor_type": "pc",
                            "actor_id": pc["id"],
                        }
                    ],
                    proposed_memories=[
                        {
                            "scope": "npc_interaction",
                            "importance": 3,
                            "text": "周怀民陪林若川前往仓库。",
                            "pc_id": pc["id"],
                            "npc_id": npc["id"],
                        }
                    ],
                    proposed_npc_updates=[
                        {
                            "npc_id": npc["id"],
                            "appeared": True,
                            "relationship_delta": 1,
                            "last_seen_time": "1928-10-03 20:00",
                            "note": "一起调查仓库。",
                        }
                    ],
                    proposed_map_moves=[
                        {
                            "token_id": token["id"],
                            "to_location_name": "仓库",
                            "reason": "玩家宣布前往仓库",
                        }
                    ],
                )

                approved = repo.approve_turn_proposal(proposal["id"])
                event_rows = connection.execute(
                    "SELECT * FROM events WHERE campaign_id = ? ORDER BY created_at, rowid",
                    (campaign["id"],),
                ).fetchall()
                npc_link = connection.execute(
                    "SELECT * FROM campaign_npcs WHERE campaign_id = ? AND npc_id = ?",
                    (campaign["id"], npc["id"]),
                ).fetchone()

                self.assertEqual(approved["status"], "approved")
                self.assertEqual(repo.get_map_token(token["id"])["location_name"], "仓库")
                self.assertEqual(npc_link["relationship_score"], 3)
                self.assertEqual(
                    {row["event_type"] for row in event_rows},
                    {"kp_turn", "clue_found", "npc_updated", "map_token_moved"},
                )
                kp_turn = next(row for row in event_rows if row["event_type"] == "kp_turn")
                self.assertNotIn("kp_notes", json.loads(kp_turn["payload_json"]))
                npc_event = next(row for row in event_rows if row["event_type"] == "npc_updated")
                self.assertEqual(npc_event["visibility"], "kp")
                move_event = next(row for row in event_rows if row["event_type"] == "map_token_moved")
                self.assertNotIn("reason", json.loads(move_event["payload_json"]))

    def test_invalid_cross_campaign_move_rolls_back_entire_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "test.sqlite3") as connection:
                repo = Repository(connection)
                campaign_a = repo.create_campaign("A")
                campaign_b = repo.create_campaign("B")
                map_b = repo.create_map(
                    campaign_b["id"],
                    generate_map("B 图", "起点、终点", ["起点", "终点"]),
                )
                token_b = repo.place_map_token(map_b["id"], "B 棋子", "起点")
                proposal = repo.create_turn_proposal(
                    campaign_id=campaign_a["id"],
                    player_action="移动",
                    public_narration="准备移动。",
                    proposed_events=[{"event_type": "before_move", "summary": "移动前事件。"}],
                    proposed_memories=[{"scope": "campaign_fact", "text": "不应被保存。"}],
                    proposed_map_moves=[
                        {
                            "token_id": token_b["id"],
                            "to_location_name": "终点",
                            "reason": "跨团错误",
                        }
                    ],
                )

                with self.assertRaises(ValueError):
                    repo.approve_turn_proposal(proposal["id"])

                self.assertEqual(repo.get_turn_proposal(proposal["id"])["status"], "draft")
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) AS total FROM events WHERE campaign_id = ?",
                        (campaign_a["id"],),
                    ).fetchone()["total"],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) AS total FROM memories WHERE campaign_id = ?",
                        (campaign_a["id"],),
                    ).fetchone()["total"],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
