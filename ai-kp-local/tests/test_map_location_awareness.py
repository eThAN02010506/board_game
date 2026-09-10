"""Tests for per-player map location awareness (current/seen/unknown)."""

from pathlib import Path

from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.maps.generation import generate_map


def _world(db_path: Path):
    context = db_session(db_path)
    connection = context.__enter__()
    repo = Repository(connection)
    campaign = repo.create_campaign("位置感知测试")
    profile = repo.create_player_profile("位置感知玩家")
    saved_map = repo.create_map(
        campaign["id"],
        generate_map(
            title="末班电车",
            prompt="车厢",
            location_names=["7号车厢", "6号车厢", "5号车厢", "4号车厢"],
            routes=[
                ("7号车厢", "6号车厢"),
                ("6号车厢", "5号车厢"),
                ("5号车厢", "4号车厢"),
            ],
        ),
    )
    return context, repo, campaign, saved_map, profile["profile"]["id"]


def test_refresh_marks_current_and_direct_neighbors_seen(tmp_path: Path) -> None:
    context, repo, campaign, saved_map, profile_id = _world(tmp_path / "awareness.sqlite3")
    try:
        map_id = saved_map["id"]
        locations = repo.connection.execute(
            "SELECT id, name FROM map_locations WHERE map_id = ?",
            (map_id,),
        ).fetchall()
        by_name = {str(row["name"]): str(row["id"]) for row in locations}
        assert {"7号车厢", "6号车厢", "5号车厢", "4号车厢"} <= set(by_name)

        # 玩家在 5 号车厢（两侧邻居：6 号、4 号；7 号未知）
        states = repo.refresh_player_location_awareness(
            map_id,
            campaign["id"],
            profile_id,
            by_name["5号车厢"],
        )
        by_state = {str(item["state"]) for item in states}
        assert "current" in by_state
        assert "seen" in by_state

        current = [item for item in states if item["state"] == "current"]
        assert len(current) == 1
        assert current[0]["location_id"] == by_name["5号车厢"]

        seen_ids = {str(item["location_id"]) for item in states if item["state"] == "seen"}
        assert seen_ids == {by_name["6号车厢"], by_name["4号车厢"]}

        # 未出现的 7 号车厢保持无记录（前端视为 unknown）。
        aware_ids = {str(item["location_id"]) for item in states}
        assert by_name["7号车厢"] not in aware_ids

        # 再次刷新保持幂等：重复调用不产生重复行
        states2 = repo.refresh_player_location_awareness(
            map_id,
            campaign["id"],
            profile_id,
            by_name["5号车厢"],
        )
        assert len(states2) == len(states)
    finally:
        context.__exit__(None, None, None)


def test_moving_to_another_location_updates_current_and_seen(tmp_path: Path) -> None:
    context, repo, campaign, saved_map, profile_id = _world(tmp_path / "awareness-move.sqlite3")
    try:
        map_id = saved_map["id"]
        locations = repo.connection.execute(
            "SELECT id, name FROM map_locations WHERE map_id = ?",
            (map_id,),
        ).fetchall()
        by_name = {str(row["name"]): str(row["id"]) for row in locations}

        repo.refresh_player_location_awareness(
            map_id,
            campaign["id"],
            profile_id,
            by_name["6号车厢"],
        )
        states = repo.refresh_player_location_awareness(
            map_id,
            campaign["id"],
            profile_id,
            by_name["4号车厢"],
        )
        by_state = {str(item["state"]) for item in states}
        assert "current" in by_state
        # 4 号的邻居 5 号应为 seen；之前见过的 6 号保持 seen
        seen_ids = {str(item["location_id"]) for item in states if item["state"] == "seen"}
        assert by_name["5号车厢"] in seen_ids
        assert by_name["6号车厢"] in seen_ids
    finally:
        context.__exit__(None, None, None)


def test_player_map_projection_omits_unknown_locations_and_geometry(tmp_path: Path) -> None:
    context, repo, campaign, saved_map, profile_id = _world(
        tmp_path / "awareness-projection.sqlite3"
    )
    try:
        map_id = saved_map["id"]
        locations = repo.connection.execute(
            "SELECT id, name FROM map_locations WHERE map_id = ?",
            (map_id,),
        ).fetchall()
        by_name = {str(row["name"]): str(row["id"]) for row in locations}
        states = repo.refresh_player_location_awareness(
            map_id,
            campaign["id"],
            profile_id,
            by_name["5号车厢"],
        )
        known = frozenset(
            str(item["location_id"])
            for item in states
            if item["state"] in {"current", "seen"}
        )

        projected = repo.get_map(
            map_id,
            allowed_visibility=("player", "table"),
            known_location_ids=known,
        )

        names = {item["name"] for item in projected["locations"]}
        assert names == {"6号车厢", "5号车厢", "4号车厢"}
        assert "7号车厢" not in projected["svg_text"]
        assert "7号车厢" not in str(projected["map_spec"])
        assert all(
            route["start_location_id"] in known
            and route["end_location_id"] in known
            for route in projected["routes"]
        )
        assert projected["render"]["background_asset_url"] is None
    finally:
        context.__exit__(None, None, None)


def test_hidden_route_does_not_reveal_its_neighbor(tmp_path: Path) -> None:
    context, repo, campaign, saved_map, profile_id = _world(
        tmp_path / "awareness-hidden-route.sqlite3"
    )
    try:
        map_id = saved_map["id"]
        locations = repo.connection.execute(
            "SELECT id, name FROM map_locations WHERE map_id = ?",
            (map_id,),
        ).fetchall()
        by_name = {str(row["name"]): str(row["id"]) for row in locations}
        repo.connection.execute(
            """
            UPDATE map_routes SET visibility = 'kp'
            WHERE map_id = ?
              AND ((start_location_id = ? AND end_location_id = ?)
                OR (start_location_id = ? AND end_location_id = ?))
            """,
            (
                map_id,
                by_name["5号车厢"],
                by_name["4号车厢"],
                by_name["4号车厢"],
                by_name["5号车厢"],
            ),
        )

        states = repo.refresh_player_location_awareness(
            map_id,
            campaign["id"],
            profile_id,
            by_name["5号车厢"],
        )

        seen_ids = {
            str(item["location_id"])
            for item in states
            if item["state"] == "seen"
        }
        assert by_name["6号车厢"] in seen_ids
        assert by_name["4号车厢"] not in seen_ids
    finally:
        context.__exit__(None, None, None)
