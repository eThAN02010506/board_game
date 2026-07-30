from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.private_random_resolution_service import (
    HiddenAppearanceCommand,
    HiddenAppearanceDestination,
    PrivateRandomResolutionService,
)
from ai_kp.application.travel_graph_service import (
    TravelGraphService,
    TravelLocationCommand,
    TravelRouteCommand,
)
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db


def setup_hidden_roll(tmp_path: Path):
    connection = connect(tmp_path / "hidden-roll.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    campaign = repo.create_campaign("暗骰测试", current_time="1928-01-01")
    npc = repo.create_npc("码头线人", home_location="旧码头", profession="记者")
    repo.link_npc_to_campaign(campaign["id"], npc["id"])
    repo.save_npc_availability_profile(
        npc["id"],
        lifecycle_state="active",
        born_year=1880,
        died_year=1960,
        active_from_year=1900,
        active_until_year=1950,
        location_tags=["旧码头"],
        profession_tags=["记者"],
        kp_notes="",
    )
    graph = TravelGraphService(repo)
    origin = graph.create_location(campaign["id"], TravelLocationCommand("旧码头"))
    station = graph.create_location(campaign["id"], TravelLocationCommand("中央车站"))
    town = graph.create_location(campaign["id"], TravelLocationCommand("邻镇"))
    graph.create_route(
        campaign["id"],
        TravelRouteCommand(origin["id"], station["id"], 20),
    )
    graph.create_route(
        campaign["id"],
        TravelRouteCommand(station["id"], town["id"], 40),
    )
    return connection, repo, campaign, npc


def test_hidden_roll_filters_destinations_and_is_idempotent(tmp_path: Path) -> None:
    connection, repo, campaign, npc = setup_hidden_roll(tmp_path)
    calls = iter((24, 2))
    service = PrivateRandomResolutionService(repo, randbelow=lambda upper: next(calls))
    command = HiddenAppearanceCommand(
        idempotency_key="trigger-0001",
        npc_id=npc["id"],
        trigger_text="调查员抵达车站",
        appearance_chance=25,
        destinations=(
            HiddenAppearanceDestination("中央车站", 1),
            HiddenAppearanceDestination("邻镇", 3),
            HiddenAppearanceDestination("不存在地点", 100),
        ),
        profession_hint="记者",
    )
    try:
        result = service.resolve_hidden_appearance(
            campaign["id"], command, created_by_member_id="kp_test"
        )
        assert result["appearance_roll"] == 25
        assert result["appears"] is True
        assert result["selected_location_name"] == "邻镇"
        assert [item["location_name"] for item in result["eligible_locations"]] == [
            "中央车站",
            "邻镇",
        ]
        assert result["public_result"] == {
            "appears": True,
            "location_name": "邻镇",
        }
        replay = service.resolve_hidden_appearance(
            campaign["id"], command, created_by_member_id="kp_test"
        )
        assert replay["id"] == result["id"]
        with pytest.raises(ValueError, match="different hidden-roll input"):
            service.resolve_hidden_appearance(
                campaign["id"],
                HiddenAppearanceCommand(
                    **{**command.__dict__, "appearance_chance": 90}
                ),
                created_by_member_id="kp_test",
            )
    finally:
        connection.close()


def test_deterministic_gate_rejects_before_rng(tmp_path: Path) -> None:
    connection, repo, campaign, npc = setup_hidden_roll(tmp_path)
    repo.save_npc_availability_profile(
        npc["id"],
        lifecycle_state="unavailable",
        born_year=None,
        died_year=None,
        active_from_year=None,
        active_until_year=None,
        location_tags=["旧码头"],
        profession_tags=[],
        kp_notes="",
    )
    called = False

    def forbidden_rng(_upper: int) -> int:
        nonlocal called
        called = True
        return 0

    try:
        with pytest.raises(ValueError, match="Deterministic NPC gate rejected"):
            PrivateRandomResolutionService(repo, randbelow=forbidden_rng).resolve_hidden_appearance(
                campaign["id"],
                HiddenAppearanceCommand(
                    idempotency_key="trigger-0002",
                    npc_id=npc["id"],
                    trigger_text="进入码头",
                    appearance_chance=100,
                    destinations=(HiddenAppearanceDestination("旧码头"),),
                ),
                created_by_member_id="kp_test",
            )
        assert called is False
        assert repo.list_npc_hidden_appearance_resolutions(campaign["id"]) == []
    finally:
        connection.close()


def test_hidden_roll_api_is_kp_only(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "hidden-roll-api.sqlite3",
        admin_token="hidden-admin",
        local_admin_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        admin = {"X-AI-KP-Admin-Token": "hidden-admin"}
        campaign = client.post(
            "/campaigns", headers=admin, json={"title": "权限测试"}
        ).json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            headers=admin,
            json={"kp_display_name": "KP"},
        ).json()
        player = client.post(
            "/sessions/join",
            json={"join_code": session["join_code"], "display_name": "玩家"},
        ).json()
        player_headers = {"Authorization": f"Bearer {player['access_token']}"}
        path = f"/campaigns/{campaign['id']}/npc-hidden-appearances"
        assert client.get(path, headers=player_headers).status_code == 403
        assert (
            client.post(
                path,
                headers=player_headers,
                json={
                    "idempotency_key": "player-0001",
                    "npc_id": "npc_unknown",
                    "trigger_text": "偷看",
                    "appearance_chance": 50,
                    "destinations": [{"location_name": "码头"}],
                },
            ).status_code
            == 403
        )
