import tempfile
import unittest
from pathlib import Path

from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository


def count_events(repo: Repository, campaign_id: str) -> int:
    row = repo.connection.execute(
        "SELECT COUNT(*) AS total FROM events WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()
    return int(row["total"])


class TurnProposalTests(unittest.TestCase):
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
