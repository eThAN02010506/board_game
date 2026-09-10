import tempfile
import unittest
from pathlib import Path

from ai_kp.application.fact_service import AssertWorldFactCommand, FactService
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import ManualProposalCommand, TurnService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.platform.modules.ingestion import ModuleChunk


def count_events(repo: Repository, campaign_id: str) -> int:
    row = repo.connection.execute(
        "SELECT COUNT(*) AS total FROM events WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()
    return int(row["total"])


class TurnProposalTests(unittest.TestCase):
    def test_ai_kp_replaces_changed_authoritative_fact_instead_of_dropping_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "facts.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("自动事实修订")
                session = SessionService(repo).create(campaign["id"])
                identity = repo.authenticate_access_token(session["access_token"])
                assert identity is not None
                module = repo.create_module(
                    campaign["id"],
                    "测试模组",
                    [
                        ModuleChunk(
                            title="入口",
                            text="门起初锁着。",
                            visibility="kp",
                            order_index=0,
                        )
                    ],
                )
                run = repo.start_campaign_module_run(
                    campaign_id=campaign["id"],
                    module_id=module["id"],
                    current_scene_key="入口",
                    active_spoiler_tags=[],
                    state={},
                    started_by_member_id=identity.member_id,
                )
                repo.set_module_run_automation_level(
                    run["id"],
                    expected_version=run["version"],
                    level="ai_kp",
                    reason="测试自动事实替换",
                    member_id=identity.member_id,
                )
                FactService(repo).assert_fact(
                    campaign["id"],
                    identity,
                    AssertWorldFactCommand(
                        fact_type="canonical_fact",
                        subject="档案室门",
                        predicate="状态",
                        object_text="锁住",
                    ),
                )
                proposal = TurnService(repo).create_manual_proposal(
                    campaign["id"],
                    identity,
                    ManualProposalCommand(
                        player_action="我用钥匙开门。",
                        public_narration="门锁咔哒一声打开了。",
                        proposed_facts=(
                            {
                                "fact_type": "canonical_fact",
                                "subject": "档案室门",
                                "predicate": "状态",
                                "object_text": "打开",
                            },
                        ),
                    ),
                )

                TurnService(repo).approve(
                    proposal["id"], campaign["id"], identity, note="自动结算"
                )

                heads = repo.list_fact_heads(campaign["id"])
                active = [entry for entry in heads if entry.active]
                self.assertEqual(len(active), 1)
                self.assertEqual(active[0].fact.object_text, "打开")
                history = repo.list_fact_entries(campaign["id"])
                self.assertEqual(len(history), 3)
                self.assertEqual(
                    [entry.fact.category for entry in history].count("retconned"),
                    1,
                )

    def test_draft_proposal_does_not_write_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")

                proposal = repo.create_turn_proposal(
                    campaign_id=campaign["id"],
                    player_action="我检查仓库。",
                    public_narration="仓库里有潮湿的木箱。",
                )

                self.assertEqual(proposal["status"], "draft")
                self.assertEqual(count_events(repo, campaign["id"]), 0)

    def test_approved_proposal_writes_event_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                proposal = repo.create_turn_proposal(
                    campaign_id=campaign["id"],
                    player_action="我检查仓库。",
                    public_narration="仓库里有潮湿的木箱。",
                    proposed_memories=[
                        {
                            "scope": "campaign_fact",
                            "importance": 3,
                            "text": "玩家在废弃仓库发现潮湿木箱。",
                        }
                    ],
                )

                approved = repo.approve_turn_proposal(proposal["id"], actor="human_kp")
                memories = repo.connection.execute(
                    "SELECT * FROM memories WHERE campaign_id = ?",
                    (campaign["id"],),
                ).fetchall()

                self.assertEqual(approved["status"], "approved")
                self.assertEqual(count_events(repo, campaign["id"]), 1)
                self.assertEqual(len(memories), 1)
                self.assertIn("潮湿木箱", memories[0]["text"])

    def test_rejected_proposal_does_not_write_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                proposal = repo.create_turn_proposal(
                    campaign_id=campaign["id"],
                    player_action="我检查仓库。",
                    public_narration="仓库里有隐藏祭坛。",
                )

                rejected = repo.reject_turn_proposal(proposal["id"], note="太早揭示秘密。")

                self.assertEqual(rejected["status"], "rejected")
                self.assertEqual(count_events(repo, campaign["id"]), 0)

    def test_override_approved_proposal_writes_human_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                proposal = repo.create_turn_proposal(
                    campaign_id=campaign["id"],
                    player_action="我检查仓库。",
                    public_narration="仓库里有隐藏祭坛。",
                )

                approved = repo.approve_turn_proposal(
                    proposal["id"],
                    actor="human_kp",
                    override_public_narration="仓库里有潮湿的异味和凌乱脚印。",
                )
                event = repo.connection.execute(
                    "SELECT * FROM events WHERE campaign_id = ?",
                    (campaign["id"],),
                ).fetchone()

                self.assertEqual(approved["status"], "approved")
                self.assertIn("潮湿的异味", event["summary"])
                self.assertTrue(any(action["action_type"] == "overridden" for action in approved["actions"]))

    def test_repository_cannot_bypass_unresolved_check_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("未决检定边界")
                proposal = repo.create_turn_proposal(
                    campaign_id=campaign["id"],
                    player_action="我搜索房间。",
                    public_narration="进行侦查检定。",
                    proposed_checks=[
                        {
                            "skill": "侦查",
                            "difficulty": "regular",
                            "reason": "寻找线索",
                        }
                    ],
                    proposed_events=[
                        {
                            "event_type": "clue_found",
                            "summary": "检定前就写入了线索。",
                        }
                    ],
                )

                with self.assertRaisesRegex(ValueError, "unresolved checks"):
                    repo.approve_turn_proposal(proposal["id"], actor="human_kp")

                restored = repo.get_turn_proposal(proposal["id"])
                self.assertEqual(restored["status"], "draft")
                self.assertEqual(count_events(repo, campaign["id"]), 0)


if __name__ == "__main__":
    unittest.main()
