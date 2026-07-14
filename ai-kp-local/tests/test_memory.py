import tempfile
import unittest
from pathlib import Path

from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.memory.npc_candidates import NpcCandidateService
from ai_kp.memory.retrieval import MemoryRetriever


class MemoryTests(unittest.TestCase):
    def test_pc_memory_can_be_retrieved_by_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                pc = repo.create_pc(campaign["id"], "林若川")
                repo.add_memory(
                    campaign_id=campaign["id"],
                    pc_id=pc["id"],
                    scope="pc_major",
                    importance=5,
                    text="林若川认识旧码头的报社线人周怀民。",
                )

                results = MemoryRetriever(connection).retrieve(
                    "我想找旧码头认识的线人",
                    campaign_id=campaign["id"],
                    pc_id=pc["id"],
                )

                self.assertEqual(results[0].scope, "pc_major")
                self.assertIn("周怀民", results[0].text)

    def test_npc_candidate_requires_reasonable_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                npc = repo.create_npc("周怀民", home_location="雾港旧码头", profession="报社线人")
                repo.link_npc_to_campaign(
                    campaign_id=campaign["id"],
                    npc_id=npc["id"],
                    relationship_score=1,
                    notes="曾提供码头走私线索。",
                )

                results = NpcCandidateService(connection).find_candidates(
                    campaign_id=campaign["id"],
                    action_text="寻找行业内打过交道的人",
                    location="旧码头",
                    profession_hint="线人",
                )

                self.assertEqual(results[0].name, "周怀民")
                self.assertGreaterEqual(results[0].score, 4)


if __name__ == "__main__":
    unittest.main()

