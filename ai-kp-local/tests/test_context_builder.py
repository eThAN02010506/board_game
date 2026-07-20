import tempfile
import unittest
from pathlib import Path

from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.kp.context_builder import ContextBuilder
from ai_kp.maps.generation import generate_map
from ai_kp.modules.ingestion import chunk_plaintext_module


class ContextBuilderTests(unittest.TestCase):
    def test_real_case_context_is_explainable_and_spoiler_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "test.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928", current_time="1928-10-03 19:30")
                pc = repo.create_pc(campaign["id"], "林若川", {"侦查": 65})
                repo.add_memory(
                    campaign_id=campaign["id"],
                    pc_id=pc["id"],
                    scope="pc_major",
                    importance=5,
                    text="林若川曾在旧码头与报社线人周怀民合作。",
                )
                npc = repo.create_npc("周怀民", home_location="旧码头", profession="报社线人")
                repo.link_npc_to_campaign(
                    campaign["id"], npc["id"], relationship_score=2, notes="曾一起追查走私线索。"
                )
                chunks = chunk_plaintext_module(
                    """@visibility=kp @spoiler=chapter-1 @scene=旧码头
仓库外的脚印可以被调查。

@visibility=kp @spoiler=ending @scene=旧码头
最终反派的真实身份。""",
                    title="雾港之夜",
                )
                repo.create_module(campaign["id"], "雾港之夜", chunks)
                saved_map = repo.create_map(
                    campaign["id"],
                    generate_map(
                        "旧码头",
                        "旧码头、仓库",
                        ["旧码头", "仓库"],
                        [("旧码头", "仓库")],
                    ),
                )
                token = repo.place_map_token(
                    saved_map["id"], "林若川", "旧码头", actor_id=pc["id"]
                )

                context = ContextBuilder(connection).build(
                    campaign_id=campaign["id"],
                    pc_id=pc["id"],
                    player_action="旧码头",
                    location="旧码头",
                    map_id=saved_map["id"],
                    profession_hint="线人",
                    active_spoiler_tags=("chapter-1",),
                )

                included_kinds = {item["kind"] for item in context.included_sources}
                self.assertIn("memory", included_kinds)
                self.assertIn("npc_candidate", included_kinds)
                self.assertIn("player_character", included_kinds)
                self.assertIn("map_token", included_kinds)
                self.assertTrue(any("脚印" in item["content"] for item in context.included_sources))
                self.assertFalse(any("反派" in item["content"] for item in context.included_sources))
                self.assertTrue(
                    any(item.get("excluded_reason") == "spoiler_not_active" for item in context.excluded_sources)
                )
                self.assertGreater(context.token_estimate, 0)
                self.assertIn("中文行动必须用中文回答", context.messages[0]["content"])
                prompt = context.messages[-1]["content"]
                self.assertIn(pc["id"], prompt)
                self.assertIn(npc["id"], prompt)
                self.assertIn(token["id"], prompt)

                player_context = ContextBuilder(connection).build(
                    campaign_id=campaign["id"],
                    pc_id=pc["id"],
                    player_action="旧码头",
                    map_id=saved_map["id"],
                    visibility_scope="player",
                )
                self.assertFalse(
                    any(item["kind"] == "npc_candidate" for item in player_context.included_sources)
                )
                self.assertNotIn("侦查", player_context.messages[-1]["content"])

    def test_context_assembly_is_saved_with_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "test.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                context = ContextBuilder(connection).build(
                    campaign_id=campaign["id"],
                    player_action="我检查仓库门。",
                )
                proposal = repo.create_turn_proposal(
                    campaign_id=campaign["id"],
                    player_action="我检查仓库门。",
                    public_narration="门上有新鲜刮痕。",
                    source_model="test-model",
                )
                saved = repo.create_context_assembly(
                    proposal_id=proposal["id"],
                    campaign_id=campaign["id"],
                    visibility_scope=context.visibility_scope,
                    final_prompt=context.messages,
                    included_sources=context.included_sources,
                    excluded_sources=context.excluded_sources,
                    token_estimate=context.token_estimate,
                )

                loaded = repo.get_context_assembly(proposal["id"])
                self.assertEqual(saved["id"], loaded["id"])
                self.assertEqual(loaded["proposal_id"], proposal["id"])
                self.assertEqual(loaded["final_prompt"][1]["role"], "user")
                self.assertTrue(any(item["kind"] == "campaign" for item in loaded["included_sources"]))


if __name__ == "__main__":
    unittest.main()
