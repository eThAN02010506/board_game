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

    def test_memory_retrieval_isolated_by_campaign_pc_and_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                other_campaign = repo.create_campaign("北境 1931")
                pc = repo.create_pc(campaign["id"], "林若川")
                other_pc = repo.create_pc(campaign["id"], "顾清")
                other_campaign_pc = repo.create_pc(other_campaign["id"], "许舟")

                own_memory = repo.add_memory(
                    campaign_id=campaign["id"],
                    pc_id=pc["id"],
                    scope="pc_major",
                    importance=3,
                    visibility="player",
                    text="旧码头的个人线索。",
                )
                repo.add_memory(
                    campaign_id=campaign["id"],
                    pc_id=other_pc["id"],
                    scope="pc_major",
                    importance=5,
                    visibility="player",
                    text="旧码头的其他角色秘密。",
                )
                shared_memory = repo.add_memory(
                    campaign_id=campaign["id"],
                    scope="clue",
                    importance=2,
                    visibility="table",
                    text="旧码头的公开钟声。",
                )
                repo.add_memory(
                    campaign_id=campaign["id"],
                    scope="clue",
                    importance=5,
                    visibility="kp",
                    text="旧码头的 KP 秘密。",
                )
                repo.add_memory(
                    campaign_id=other_campaign["id"],
                    pc_id=other_campaign_pc["id"],
                    scope="pc_major",
                    importance=5,
                    visibility="player",
                    text="旧码头的异团秘密。",
                )

                results = MemoryRetriever(connection).retrieve(
                    "旧码头",
                    campaign_id=campaign["id"],
                    pc_id=pc["id"],
                    visibility=("player", "table"),
                )

                self.assertEqual(
                    {item.id for item in results},
                    {own_memory["id"], shared_memory["id"]},
                )

    def test_memory_retrieval_handles_empty_inputs_and_single_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                memory = repo.add_memory(
                    campaign_id=campaign["id"],
                    scope="clue",
                    visibility="kp",
                    text="旧码头的密门。",
                )
                retriever = MemoryRetriever(connection)

                single_visibility = retriever.retrieve(
                    "旧码头",
                    campaign_id=campaign["id"],
                    visibility="kp",
                )

                self.assertEqual([item.id for item in single_visibility], [memory["id"]])
                self.assertEqual(
                    retriever.retrieve(
                        "旧码头",
                        campaign_id=campaign["id"],
                        visibility=(),
                    ),
                    [],
                )
                self.assertEqual(
                    retriever.retrieve(
                        "旧码头",
                        campaign_id=campaign["id"],
                        limit=0,
                    ),
                    [],
                )
                self.assertEqual(
                    retriever.retrieve(
                        "旧码头",
                        campaign_id=campaign["id"],
                        limit=-1,
                    ),
                    [],
                )
                self.assertEqual(
                    retriever.retrieve(
                        "　",
                        campaign_id=campaign["id"],
                    ),
                    [],
                )

    def test_memory_ranking_is_stable_when_scores_and_timestamps_tie(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                first = repo.add_memory(
                    campaign_id=campaign["id"],
                    scope="clue",
                    importance=2,
                    text="旧码头的线索甲。",
                )
                second = repo.add_memory(
                    campaign_id=campaign["id"],
                    scope="clue",
                    importance=2,
                    text="旧码头的线索乙。",
                )
                connection.execute(
                    """
                    UPDATE memories
                    SET created_at = '1928-10-03 19:30:00'
                    WHERE id IN (?, ?)
                    """,
                    (first["id"], second["id"]),
                )
                expected_ids = sorted((first["id"], second["id"]))
                retriever = MemoryRetriever(connection)

                first_run = retriever.retrieve("旧码头", campaign_id=campaign["id"])
                second_run = retriever.retrieve("旧码头", campaign_id=campaign["id"])

                self.assertEqual([item.id for item in first_run], expected_ids)
                self.assertEqual([item.id for item in second_run], expected_ids)

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

    def test_npc_candidates_match_chinese_notes_and_normalize_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "test.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                npc = repo.create_npc(
                    "Marlow",
                    home_location="North DOCK",
                    profession="Press Informant",
                )
                repo.link_npc_to_campaign(
                    campaign_id=campaign["id"],
                    npc_id=npc["id"],
                    notes="曾帮助调查码头走私账册。",
                )

                results = NpcCandidateService(connection).find_candidates(
                    campaign_id=campaign["id"],
                    action_text="我想继续调查走私账册",
                    location=" north dock ",
                    profession_hint=" press informant ",
                )

                self.assertEqual([item.npc_id for item in results], [npc["id"]])
                self.assertEqual(results[0].score, 6)
                self.assertIn("past notes overlap with action", results[0].reason)

    def test_npc_candidate_limit_and_tie_order_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "test.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                for name in ("周乙", "周甲"):
                    npc = repo.create_npc(name)
                    repo.link_npc_to_campaign(
                        campaign_id=campaign["id"],
                        npc_id=npc["id"],
                        relationship_score=1,
                    )
                service = NpcCandidateService(connection)

                first_run = service.find_candidates(
                    campaign_id=campaign["id"],
                    action_text="寻找旧识",
                )
                second_run = service.find_candidates(
                    campaign_id=campaign["id"],
                    action_text="寻找旧识",
                )

                self.assertEqual(
                    [item.name for item in first_run],
                    ["周乙", "周甲"],
                )
                self.assertEqual(
                    [item.npc_id for item in first_run],
                    [item.npc_id for item in second_run],
                )
                self.assertEqual(
                    service.find_candidates(
                        campaign_id=campaign["id"],
                        action_text="寻找旧识",
                        limit=0,
                    ),
                    [],
                )


if __name__ == "__main__":
    unittest.main()
