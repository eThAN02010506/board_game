import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.maps.generation import (
    GeneratedLocation,
    GeneratedMap,
    GeneratedRoute,
    generate_map,
    render_svg,
)


class MapGenerationTests(unittest.TestCase):
    def test_player_map_svg_does_not_contain_kp_locations_or_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "test.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                draft = GeneratedMap(
                    title="仓库",
                    prompt="秘密地窖在仓库下面",
                    style="investigation",
                    width=800,
                    height=600,
                    locations=[
                        GeneratedLocation("仓库大厅", 200, 300, "table"),
                        GeneratedLocation("秘密地窖", 600, 300, "kp"),
                    ],
                    routes=[GeneratedRoute("仓库大厅", "秘密地窖", visibility="kp")],
                )
                generated = GeneratedMap(**{**draft.__dict__, "svg_text": render_svg(draft)})
                saved = repo.create_map(campaign["id"], generated)

                player_map = repo.get_map(saved["id"], allowed_visibility=("player", "table"))

                self.assertEqual(player_map["prompt"], "")
                self.assertNotIn("秘密地窖", player_map["svg_text"])
                self.assertEqual([item["name"] for item in player_map["locations"]], ["仓库大厅"])

    def test_generated_map_is_saved_with_svg_locations_and_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                generated = generate_map(
                    title="旧码头区域图",
                    prompt="旧码头、废弃仓库、报社、警局",
                    location_names=["旧码头", "废弃仓库", "报社", "警局"],
                    routes=[("旧码头", "废弃仓库"), ("旧码头", "报社"), ("报社", "警局")],
                )

                saved_map = repo.create_map(campaign["id"], generated)

                self.assertEqual(saved_map["title"], "旧码头区域图")
                self.assertIn("<svg", saved_map["svg_text"])
                self.assertEqual(len(saved_map["locations"]), 4)
                self.assertEqual(len(saved_map["routes"]), 3)

    def test_table_style_token_movement_is_saved_as_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                generated = generate_map(
                    title="旧码头区域图",
                    prompt="旧码头、废弃仓库、报社",
                    location_names=["旧码头", "废弃仓库", "报社"],
                    routes=[("旧码头", "废弃仓库")],
                )
                saved_map = repo.create_map(campaign["id"], generated)
                token = repo.place_map_token(
                    map_id=saved_map["id"],
                    label="林若川",
                    location_name="旧码头",
                    actor_type="pc",
                    color="#2f6db3",
                )

                moved = repo.move_map_token(
                    token_id=token["id"],
                    to_location_name="废弃仓库",
                    moved_by="player",
                    note="玩家决定检查仓库。",
                )
                moves = repo.list_map_token_moves(token["id"])

                self.assertEqual(moved["location_name"], "废弃仓库")
                self.assertEqual(len(moves), 2)
                self.assertIsNone(moves[0]["from_location_name"])
                self.assertEqual(moves[1]["from_location_name"], "旧码头")
                self.assertEqual(moves[1]["to_location_name"], "废弃仓库")

    def test_token_cannot_move_without_route_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                generated = generate_map(
                    title="旧码头区域图",
                    prompt="旧码头、废弃仓库、报社",
                    location_names=["旧码头", "废弃仓库", "报社"],
                    routes=[("旧码头", "废弃仓库")],
                )
                saved_map = repo.create_map(campaign["id"], generated)
                token = repo.place_map_token(
                    map_id=saved_map["id"],
                    label="林若川",
                    location_name="旧码头",
                )

                with self.assertRaises(ValueError):
                    repo.move_map_token(token["id"], "报社")

    def test_map_revision_and_fog_are_versioned_without_leaking_revealed_fog(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "map-editing.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("地图编辑")
                kp = repo.create_campaign_session(
                    campaign["id"], kp_display_name="地图 KP"
                )["member"]
                saved = repo.create_map(
                    campaign["id"],
                    generate_map(
                        title="旧宅",
                        prompt="门厅与书房",
                        location_names=["门厅", "书房"],
                        routes=[("门厅", "书房")],
                    ),
                )
                prior_revision_id = saved["revision_id"]
                edited_spec = deepcopy(saved["map_spec"])
                edited_spec["title"] = "旧宅·夜间"
                edited_spec["locations"][0]["position"]["x"] += 10

                revised = repo.create_map_revision_from_spec(
                    saved["id"],
                    expected_revision_id=prior_revision_id,
                    map_spec=edited_spec,
                    member_id=kp["id"],
                )

                self.assertEqual(revised["revision_no"], saved["revision_no"] + 1)
                self.assertNotEqual(revised["revision_id"], prior_revision_id)
                self.assertEqual(revised["title"], "旧宅·夜间")
                with self.assertRaisesRegex(ValueError, "refresh"):
                    repo.create_map_revision_from_spec(
                        saved["id"],
                        expected_revision_id=prior_revision_id,
                        map_spec=edited_spec,
                        member_id=kp["id"],
                    )

                fog = repo.create_map_fog_region(
                    saved["id"],
                    label="未探索书房",
                    polygon=[
                        {"x": 10, "y": 10},
                        {"x": 100, "y": 10},
                        {"x": 100, "y": 100},
                        {"x": 10, "y": 100},
                    ],
                    member_id=kp["id"],
                )
                player_map = repo.get_map(
                    saved["id"], allowed_visibility=("player", "table")
                )
                self.assertEqual(
                    [item["id"] for item in player_map["fog_regions"]], [fog["id"]]
                )

                revealed = repo.reveal_map_fog_region(
                    fog["id"], expected_version=fog["version"]
                )
                self.assertEqual(revealed["status"], "revealed")
                player_map = repo.get_map(
                    saved["id"], allowed_visibility=("player", "table")
                )
                self.assertEqual(player_map["fog_regions"], [])
                with self.assertRaisesRegex(ValueError, "refresh"):
                    repo.reveal_map_fog_region(
                        fog["id"], expected_version=fog["version"]
                    )


if __name__ == "__main__":
    unittest.main()
