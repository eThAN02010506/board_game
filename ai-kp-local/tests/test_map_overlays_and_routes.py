from copy import deepcopy
from pathlib import Path

import pytest

from ai_kp.application.map_route_plan_service import (
    CreateRoutePlanCommand,
    MapRoutePlanService,
    TokenRoute,
)
from ai_kp.application.session_service import SessionService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.maps.generation import generate_map


def _world(db_path: Path):
    context = db_session(db_path)
    connection = context.__enter__()
    repo = Repository(connection)
    campaign = repo.create_campaign("地图覆盖层测试")
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


def test_hidden_fog_is_a_revision_bound_overlay_and_scales_on_inheritance(
    tmp_path: Path,
) -> None:
    context, repo, _, identity, saved_map = _world(tmp_path / "fog.sqlite3")
    try:
        fog = repo.create_map_fog_region(
            saved_map["id"],
            label="北侧未探索",
            polygon=[
                {"x": 10, "y": 10},
                {"x": 110, "y": 10},
                {"x": 110, "y": 110},
            ],
            member_id=identity.member_id,
        )
        revised_spec = deepcopy(saved_map["map_spec"])
        revised_spec["canvas"]["width"] *= 2
        revised_spec["canvas"]["height"] *= 2
        revised = repo.create_map_revision_from_spec(
            saved_map["id"],
            expected_revision_id=saved_map["revision_id"],
            map_spec=revised_spec,
            member_id=identity.member_id,
        )

        assert revised["revision_id"] != saved_map["revision_id"]
        assert len(revised["fog_regions"]) == 1
        inherited = revised["fog_regions"][0]
        assert inherited["id"] != fog["id"]
        assert inherited["inherited_from_fog_id"] == fog["id"]
        assert inherited["polygon"][1] == {"x": 220.0, "y": 20.0}
    finally:
        context.__exit__(None, None, None)


def test_split_route_plan_completes_as_each_token_reaches_its_destination(
    tmp_path: Path,
) -> None:
    context, repo, campaign, identity, saved_map = _world(tmp_path / "routes.sqlite3")
    try:
        first_pc = repo.create_pc(campaign["id"], "甲")
        second_pc = repo.create_pc(campaign["id"], "乙")
        first = repo.place_map_token(
            saved_map["id"], "甲", "广场", actor_id=first_pc["id"]
        )
        second = repo.place_map_token(
            saved_map["id"], "乙", "广场", actor_id=second_pc["id"]
        )
        plan = MapRoutePlanService(repo).create(
            campaign_id=campaign["id"],
            map_id=saved_map["id"],
            member_id=identity.member_id,
            command=CreateRoutePlanCommand(
                title="分头调查",
                note="",
                token_routes=(
                    TokenRoute(first["id"], ("广场", "警局")),
                    TokenRoute(second["id"], ("广场", "车站")),
                ),
                player_submission=False,
            ),
        )
        assert plan["status"] == "approved"

        repo.move_map_token(
            first["id"], "警局", expected_version=first["version"]
        )
        assert repo.get_map_route_plan(plan["id"])["status"] == "executing"
        repo.move_map_token(
            second["id"], "车站", expected_version=second["version"]
        )
        completed = repo.get_map_route_plan(plan["id"])
        assert completed["status"] == "completed"
        assert {leg["status"] for leg in completed["legs"]} == {"completed"}
    finally:
        context.__exit__(None, None, None)


def test_handout_link_target_must_belong_to_same_campaign(tmp_path: Path) -> None:
    context, repo, campaign, identity, saved_map = _world(tmp_path / "links.sqlite3")
    try:
        linked = repo.create_handout(
            campaign_id=campaign["id"],
            member_id=identity.member_id,
            title="小镇地图",
            body="玩家可查看的地图线索",
            kind="clue",
            link_type="map",
            link_id=saved_map["id"],
        )
        assert linked["link_id"] == saved_map["id"]

        other_campaign = repo.create_campaign("另一个团")
        other_map = repo.create_map(
            other_campaign["id"],
            generate_map(title="异团地图", prompt="入口"),
        )
        with pytest.raises(ValueError, match="does not belong"):
            repo.create_handout(
                campaign_id=campaign["id"],
                member_id=identity.member_id,
                title="污染尝试",
                body="不应写入",
                kind="clue",
                link_type="map",
                link_id=other_map["id"],
            )
    finally:
        context.__exit__(None, None, None)
