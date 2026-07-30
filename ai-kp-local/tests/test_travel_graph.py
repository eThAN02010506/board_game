from pathlib import Path

from ai_kp.application.npc_reappearance_service import NpcReappearanceService
from ai_kp.application.travel_graph_service import (
    TravelGraphService,
    TravelLocationCommand,
    TravelRouteCommand,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db


def setup_graph(tmp_path: Path):
    connection = connect(tmp_path / "travel.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    campaign = repo.create_campaign("旅行图测试")
    return connection, repo, campaign, TravelGraphService(repo)


def test_weighted_shortest_path_uses_aliases_and_skips_blocked_route(
    tmp_path: Path,
) -> None:
    connection, _repo, campaign, service = setup_graph(tmp_path)
    try:
        harbour = service.create_location(
            campaign["id"],
            TravelLocationCommand("旧码头", aliases=("码头区",)),
        )
        station = service.create_location(
            campaign["id"],
            TravelLocationCommand("中央车站", aliases=("火车站",)),
        )
        town = service.create_location(
            campaign["id"],
            TravelLocationCommand("邻镇", aliases=("阿卡姆",)),
        )
        service.create_route(
            campaign["id"],
            TravelRouteCommand(
                harbour["id"],
                town["id"],
                300,
                travel_mode="drive",
                status="blocked",
            ),
        )
        service.create_route(
            campaign["id"],
            TravelRouteCommand(harbour["id"], station["id"], 20, travel_mode="walk"),
        )
        service.create_route(
            campaign["id"],
            TravelRouteCommand(station["id"], town["id"], 70, travel_mode="rail"),
        )

        result = service.preview(
            campaign["id"],
            origins=("码头区",),
            destination="阿卡姆",
            max_minutes=120,
        )
        assert result["status"] == "reachable"
        assert result["total_minutes"] == 90
        assert [item["name"] for item in result["locations"]] == [
            "旧码头",
            "中央车站",
            "邻镇",
        ]
        assert [item["travel_mode"] for item in result["legs"]] == ["walk", "rail"]
        many = service.resolve_destinations(
            campaign["id"],
            origins=("码头区",),
            destinations=("火车站", "阿卡姆", "未知地点"),
            max_minutes=120,
        )
        assert [item["status"] for item in many] == [
            "reachable",
            "reachable",
            "unresolved_destination",
        ]
        assert [item["total_minutes"] for item in many] == [20, 90, None]
    finally:
        connection.close()


def test_directed_route_and_limit_are_explained(tmp_path: Path) -> None:
    connection, _repo, campaign, service = setup_graph(tmp_path)
    try:
        origin = service.create_location(
            campaign["id"], TravelLocationCommand("起点")
        )
        destination = service.create_location(
            campaign["id"], TravelLocationCommand("终点")
        )
        service.create_route(
            campaign["id"],
            TravelRouteCommand(
                origin["id"],
                destination["id"],
                45,
                bidirectional=False,
            ),
        )
        over_limit = service.preview(
            campaign["id"],
            origins=("起点",),
            destination="终点",
            max_minutes=30,
        )
        assert over_limit["status"] == "over_limit"
        assert over_limit["total_minutes"] == 45
        gate = NpcReappearanceService.evaluate(
            campaign_time="1928-01-01",
            policy={
                "require_location_match": True,
                "require_profession_match": False,
            },
            profile={
                "lifecycle_state": "active",
                "born_year": 1880,
                "died_year": 1950,
                "active_from_year": 1900,
                "active_until_year": 1940,
                "location_tags": ["起点"],
                "profession_tags": [],
            },
            context_location="终点",
            profession_hint=None,
            travel_result=over_limit,
            final=True,
        )
        assert gate["decision"] == "blocked"
        assert "超过本团限制" in gate["warnings"][0]
        reverse = service.preview(
            campaign["id"],
            origins=("终点",),
            destination="起点",
            max_minutes=60,
        )
        assert reverse["status"] == "unreachable"
    finally:
        connection.close()
