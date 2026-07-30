import json
import tempfile
import unittest
from pathlib import Path

from ai_kp.application.session_service import SessionService
from ai_kp.application.simulation_replay_service import SimulationReplayService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.evaluation.simulated_campaign import run_simulation


class SimulatedCampaignTests(unittest.TestCase):
    def test_replay_is_deterministic_and_secret_requires_reveal(self) -> None:
        definition = {
            "steps": [
                {"action": "emit", "audience": "kp", "content": "凶手是管家"},
                {"action": "assert_player_not_sees", "text": "凶手"},
                {"action": "reveal", "text": "凶手"},
                {"action": "assert_player_sees", "text": "凶手"},
            ]
        }
        first = run_simulation(definition)
        second = run_simulation(definition)
        self.assertEqual(first["status"], "passed")
        self.assertEqual(first["result_fingerprint"], second["result_fingerprint"])
        self.assertEqual(first["metrics"]["passed_assertions"], 2)

    def test_service_replay_uses_product_services_and_rolls_back_world_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "service-replay.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("评测来源团")
                session = SessionService(repo).create(campaign["id"])
                identity = repo.authenticate_access_token(session["access_token"])
                self.assertIsNotNone(identity)
                definition = {
                    "mode": "service_replay",
                    "steps": [
                        {
                            "action": "generate_map",
                            "alias": "town",
                            "title": "镇中心",
                            "locations": ["广场", "警局"],
                            "routes": [["广场", "警局"]],
                        },
                        {
                            "action": "place_token",
                            "alias": "hero",
                            "map_ref": "town",
                            "label": "调查员",
                            "location": "广场",
                        },
                        {
                            "action": "plan_routes",
                            "alias": "split",
                            "map_ref": "town",
                            "token_routes": [
                                {
                                    "token_ref": "hero",
                                    "waypoints": ["广场", "警局"],
                                }
                            ],
                        },
                        {
                            "action": "move_token",
                            "token_ref": "hero",
                            "to": "警局",
                        },
                        {
                            "action": "assert_equals",
                            "ref": "hero",
                            "path": "location_name",
                            "value": "警局",
                        },
                        {
                            "action": "create_proposal",
                            "alias": "proposal",
                            "player_action": "搜查值班室",
                            "public_narration": "你发现了一本登记簿。",
                        },
                        {
                            "action": "approve_proposal",
                            "proposal_ref": "proposal",
                        },
                        {
                            "action": "assert_equals",
                            "ref": "proposal",
                            "path": "status",
                            "value": "approved",
                        },
                        {
                            "action": "create_handout",
                            "alias": "clue",
                            "title": "秘密登记簿",
                            "body": "失踪者曾在午夜来访。",
                        },
                        {
                            "action": "capture_player_handouts",
                            "alias": "before_reveal",
                        },
                        {
                            "action": "assert_not_contains",
                            "ref": "before_reveal",
                            "path": "value",
                            "value": "午夜来访",
                        },
                        {
                            "action": "update_handout",
                            "handout_ref": "clue",
                            "status": "revealed",
                        },
                        {
                            "action": "capture_player_handouts",
                            "alias": "after_reveal",
                        },
                        {
                            "action": "assert_contains",
                            "ref": "after_reveal",
                            "path": "value",
                            "value": "午夜来访",
                        },
                    ],
                }
                case = repo.create_simulation_case(
                    campaign["id"],
                    "真实服务回放",
                    definition,
                    identity.member_id,  # type: ignore[union-attr]
                )
                campaign_count = connection.execute(
                    "SELECT COUNT(*) FROM campaigns"
                ).fetchone()[0]
                result = SimulationReplayService(repo).run_case(case["id"])

                self.assertEqual(result["status"], "passed", result["trajectory"])
                self.assertEqual(result["runner_version"], "product-service-replay.v3")
                self.assertEqual(result["metrics"]["service_calls"], 10)
                self.assertEqual(result["metrics"]["passed_assertions"], 4)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],
                    campaign_count,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM maps").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM campaign_handouts"
                    ).fetchone()[0],
                    0,
                )

    def test_chang_an_service_replay_reaches_true_end_with_dual_view_logs(
        self,
    ) -> None:
        fixture_path = (
            Path(__file__).parent
            / "fixtures"
            / "simulations"
            / "chang_an_zhi_xiang.json"
        )
        definition = json.loads(fixture_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "chang-an.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("常暗之厢黄金回放")
                session = SessionService(repo).create(campaign["id"])
                identity = repo.authenticate_access_token(session["access_token"])
                self.assertIsNotNone(identity)
                case = repo.create_simulation_case(
                    campaign["id"],
                    "常暗之厢 A 结局",
                    definition,
                    identity.member_id,  # type: ignore[union-attr]
                )

                result = SimulationReplayService(repo).run_case(case["id"])

                self.assertEqual(result["status"], "passed", result["trajectory"])
                self.assertEqual(result["metrics"]["failed_assertions"], 0)
                logs = [
                    entry
                    for step in result["trajectory"]
                    for entry in step.get("log", [])
                ]
                self.assertTrue(
                    any(item["actor"] == "player" for item in logs),
                    logs,
                )
                self.assertTrue(
                    any(
                        item["actor"] == "kp" and item["audience"] == "table"
                        for item in logs
                    ),
                    logs,
                )
                self.assertTrue(
                    any(
                        item["actor"] == "model" and item["audience"] == "kp"
                        for item in logs
                    ),
                    logs,
                )
                self.assertTrue(
                    any(item["actor"] == "rules_engine" for item in logs),
                    logs,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM campaigns"
                    ).fetchone()[0],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
