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


def test_ai_kp_route_plan_auto_approves_player_submission(
    tmp_path: Path,
) -> None:
    """ai_kp 模式下玩家提交的移动计划自动置为 approved。"""
    from fastapi.testclient import TestClient

    from ai_kp.api.main import create_app
    from ai_kp.core.config import Settings
    from ai_kp.core.db import connect
    from ai_kp.platform.modules.ingestion import ModuleChunk

    db_path = tmp_path / "ai-kp-route.sqlite3"
    app = create_app(
        Settings(
            db_path=db_path,
            local_admin_enabled=False,
            admin_token="route-admin",
        )
    )
    with TestClient(app) as client:
        admin_headers = {"X-AI-KP-Admin-Token": "route-admin"}
        campaign = client.post(
            "/campaigns", headers=admin_headers, json={"title": "AI KP 路线测试"}
        ).json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            headers=admin_headers,
            json={"kp_display_name": "测试 KP"},
        ).json()
        kp_headers = {"Authorization": f"Bearer {session['access_token']}"}

        # 用 repo 创建模组 + ai_kp 自动化
        observer = connect(db_path)
        try:
            repo = Repository(observer)
            module = repo.create_module(
                campaign["id"],
                "测试模组",
                [ModuleChunk(title="入口", text="车厢里很安静。", visibility="kp", order_index=0)],
            )
            run = repo.start_campaign_module_run(
                campaign_id=campaign["id"],
                module_id=module["id"],
                current_scene_key="入口",
                active_spoiler_tags=[],
                state={},
                started_by_member_id=session["member"]["id"],
            )
            repo.set_module_run_automation_level(
                run["id"],
                expected_version=run["version"],
                level="ai_kp",
                reason="测试",
                member_id=session["member"]["id"],
            )
            observer.commit()
        finally:
            observer.close()

        # 生成并发布地图
        saved_map = client.post(
            f"/campaigns/{campaign['id']}/maps/generate",
            headers=kp_headers,
            json={
                "title": "末班电车",
                "prompt": "末班电车内部结构图",
                "locations": ["7号车厢", "6号车厢"],
                "routes": [["7号车厢", "6号车厢"]],
                "map_kind": "floorplan",
            },
        ).json()
        client.post(
            f"/maps/{saved_map['id']}/publish",
            headers=kp_headers,
            json={"expected_revision_id": saved_map["revision_id"]},
        )

        # 玩家认领席位并绑定调查员
        profile = client.post("/player-profiles", json={"display_name": "玩家"}).json()
        seat = client.post(
            f"/sessions/{session['session']['id']}/seats",
            headers=kp_headers,
            json={"label": "玩家席位"},
        ).json()
        player = client.post(
            "/session-seats/claim",
            headers={"X-AI-KP-Player-Token": profile["player_token"]},
            json={"invitation_code": seat["invitation_code"], "display_name": "玩家"},
        ).json()
        player_headers = {
            "Authorization": f"Bearer {player['access_token']}",
            "X-AI-KP-Player-Token": profile["player_token"],
        }
        inv = client.post(
            "/investigators",
            headers=player_headers,
            json={"canonical_sheet": _coc7_sheet("测试员"), "source_type": "manual"},
        ).json()
        client.post(
            f"/campaigns/{campaign['id']}/investigators/{inv['id']}/submit",
            headers=player_headers,
            json={},
        )

        # 玩家创建路线计划：ai_kp 下应自动 approved
        token = next(
            t
            for t in client.get(
                f"/maps/{saved_map['id']}?view=player", headers=player_headers
            ).json()["tokens"]
            if t["actor_type"] == "pc"
        )
        plan = client.post(
            f"/maps/{saved_map['id']}/route-plans",
            headers=player_headers,
            json={
                "title": "前往6号",
                "note": "",
                "token_routes": [
                    {"token_id": token["id"], "waypoints": ["7号车厢", "6号车厢"]}
                ],
            },
        )
        assert plan.status_code == 200, plan.text
        assert plan.json()["status"] == "approved", plan.json()


def _coc7_sheet(name: str) -> dict:
    return {
        "schema_version": "coc7-investigator-v1",
        "ruleset_id": "coc7-keeper-cn-2002c",
        "identity": {"name": name, "occupation": "调查员", "age": 30, "era": "1920s"},
        "characteristics": {k: 50 for k in ("str","con","siz","dex","app","int","pow","edu","luck")},
        "skills": [],
        "assets": {"items": []},
        "background": {},
        "provenance": {"source_type": "test"},
    }
