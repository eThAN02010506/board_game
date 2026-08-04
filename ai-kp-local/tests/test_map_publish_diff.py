"""Regression tests for the map publish-diff preview (PRD 12.1 item 5)."""

from copy import deepcopy
from pathlib import Path

import pytest

from ai_kp.application.map_service import MapService
from ai_kp.application.session_service import SessionService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.maps.generation import generate_map


def _world(db_path: Path):
    context = db_session(db_path)
    connection = context.__enter__()
    repo = Repository(connection)
    campaign = repo.create_campaign("发布差异测试")
    session = SessionService(repo).create(campaign["id"])
    identity = repo.authenticate_access_token(session["access_token"])
    assert identity is not None
    saved_map = repo.create_map(
        campaign["id"],
        generate_map(
            title="小镇",
            prompt="广场、警局、车站",
            location_names=["广场", "警局", "车站"],
            routes=[("广场", "警局"), ("广场", "车站")],
        ),
    )
    return context, repo, campaign, identity, saved_map


def _service(repo: Repository) -> MapService:
    return MapService(repo)


def _publish(repo: Repository, service: MapService, saved_map: dict, campaign: dict, identity) -> None:
    expected = saved_map["revision_id"]
    selected = saved_map.get("selected_public_asset_id")
    service.publish(
        saved_map["id"],
        campaign["id"],
        identity.session_id,
        expected_revision_id=expected,
        expected_selected_asset_id=selected,
    )


def test_first_publish_has_no_comparison_baseline(tmp_path: Path) -> None:
    context, repo, campaign, _identity, saved_map = _world(tmp_path / "first.sqlite3")
    try:
        diff = _service(repo).publish_diff(
            saved_map["id"],
            campaign["id"],
            expected_revision_id=saved_map["revision_id"],
        )
        assert diff["first_publish"] is True
        assert diff["revision"]["published_id"] is None
        assert diff["revision"]["current_no"] == 1
        assert diff["locations"]["added"] == []
        assert diff["locations"]["removed"] == []
    finally:
        context.__exit__(None, None, None)


def test_publish_records_published_revision(tmp_path: Path) -> None:
    context, repo, campaign, identity, saved_map = _world(tmp_path / "publish.sqlite3")
    try:
        _publish(repo, _service(repo), saved_map, campaign, identity)
        published = repo.get_published_revision_spec(saved_map["id"])
        assert published is not None
        assert published["revision_no"] == 1
        # A subsequent diff sees no changes (current == published).
        diff = _service(repo).publish_diff(
            saved_map["id"],
            campaign["id"],
            expected_revision_id=saved_map["revision_id"],
        )
        assert diff["first_publish"] is False
        assert diff["locations"]["added"] == []
        assert diff["locations"]["changed"] == []
        assert diff["locations"]["removed"] == []
        assert diff["player_visible_changes"] == []
    finally:
        context.__exit__(None, None, None)


def test_second_revision_diff_lists_added_removed_changed(tmp_path: Path) -> None:
    context, repo, campaign, identity, saved_map = _world(tmp_path / "second.sqlite3")
    try:
        _publish(repo, _service(repo), saved_map, campaign, identity)

        revised = deepcopy(saved_map["map_spec"])
        revised["title"] = "小镇（修订）"
        # Change an existing location's visibility and add a new one.
        for location in revised["locations"]:
            if location["name"] == "广场":
                location["visibility"] = "player"
        revised["locations"].append(
            {
                "id": "loc_new_1",
                "name": "邮局",
                "kind": "place",
                "position": {"x": 700.0, "y": 400.0},
                "visibility": "table",
                "public_description": "",
                "kp_notes": "",
                "tags": [],
                "render_policy": "structure_overlay",
            }
        )
        second = repo.create_map_revision_from_spec(
            saved_map["id"],
            expected_revision_id=saved_map["revision_id"],
            map_spec=revised,
            member_id=identity.member_id,
        )

        diff = _service(repo).publish_diff(
            saved_map["id"],
            campaign["id"],
            expected_revision_id=second["revision_id"],
        )
        assert diff["first_publish"] is False
        assert diff["revision"]["current_no"] == 2
        assert diff["revision"]["published_no"] == 1
        assert diff["title_changed"] is True
        added_names = {item["name"] for item in diff["locations"]["added"]}
        assert "邮局" in added_names
        changed_names = {item["name"] for item in diff["locations"]["changed"]}
        assert "广场" in changed_names
        # The changed location is player-visible, so it must surface.
        player_visible = {item["name"] for item in diff["player_visible_changes"]}
        assert "广场" in player_visible
        assert "邮局" in player_visible
    finally:
        context.__exit__(None, None, None)


def test_publish_diff_does_not_change_status_and_rejects_stale_revision(
    tmp_path: Path,
) -> None:
    context, repo, campaign, _identity, saved_map = _world(tmp_path / "stale.sqlite3")
    try:
        service = _service(repo)
        diff = service.publish_diff(
            saved_map["id"],
            campaign["id"],
            expected_revision_id=saved_map["revision_id"],
        )
        assert diff["first_publish"] is True
        # publish_diff must not alter draft status.
        assert repo.get_map(saved_map["id"])["status"] == "draft"

        with pytest.raises(ValueError, match="地图版本已变化"):
            service.publish_diff(
                saved_map["id"],
                campaign["id"],
                expected_revision_id="stale-revision",
            )
    finally:
        context.__exit__(None, None, None)
