from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings


def test_travel_graph_is_kp_only_and_returns_explainable_route(tmp_path) -> None:
    settings = Settings(
        db_path=tmp_path / "travel-api.sqlite3",
        admin_token="travel-admin",
        local_admin_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        admin = {"X-AI-KP-Admin-Token": "travel-admin"}
        campaign = client.post(
            "/campaigns",
            headers=admin,
            json={"title": "旅行 API 测试"},
        ).json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            headers=admin,
            json={"kp_display_name": "KP"},
        ).json()
        kp_headers = {"Authorization": f"Bearer {session['access_token']}"}
        player = client.post(
            "/sessions/join",
            json={"join_code": session["join_code"], "display_name": "玩家"},
        ).json()
        player_headers = {"Authorization": f"Bearer {player['access_token']}"}

        origin = client.post(
            f"/campaigns/{campaign['id']}/travel-locations",
            headers=kp_headers,
            json={"name": "港口", "aliases": ["码头"]},
        )
        destination = client.post(
            f"/campaigns/{campaign['id']}/travel-locations",
            headers=kp_headers,
            json={"name": "车站"},
        )
        assert origin.status_code == destination.status_code == 200
        route = client.post(
            f"/campaigns/{campaign['id']}/travel-routes",
            headers=kp_headers,
            json={
                "from_location_id": origin.json()["id"],
                "to_location_id": destination.json()["id"],
                "travel_minutes": 35,
                "travel_mode": "drive",
                "bidirectional": False,
            },
        )
        assert route.status_code == 200
        preview = client.post(
            f"/campaigns/{campaign['id']}/travel-route-preview",
            headers=kp_headers,
            json={
                "origins": ["码头"],
                "destination": "车站",
                "max_minutes": 40,
            },
        )
        assert preview.status_code == 200
        assert preview.json()["status"] == "reachable"
        assert preview.json()["total_minutes"] == 35

        assert (
            client.get(
                f"/campaigns/{campaign['id']}/travel-graph",
                headers=player_headers,
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/campaigns/{campaign['id']}/npcs",
                headers=player_headers,
            ).status_code
            == 403
        )
