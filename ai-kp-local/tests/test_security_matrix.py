"""Data-driven authorization, fault-injection and tenant-isolation regressions."""

from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient

from ai_kp.api.dependencies import get_repo
from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _campaign_session(client: TestClient, title: str) -> tuple[dict, dict, dict]:
    campaign = client.post("/campaigns", json={"title": title}).json()
    session = client.post(
        f"/campaigns/{campaign['id']}/sessions",
        json={"kp_display_name": f"{title} KP"},
    ).json()
    return campaign, session, _headers(session["access_token"])


def _join_player(client: TestClient, session: dict, name: str) -> dict[str, str]:
    bundle = client.post(
        "/sessions/join",
        json={"join_code": session["join_code"], "display_name": name},
    ).json()
    return _headers(bundle["access_token"])


def test_authorization_matrix_and_cross_campaign_isolation(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "security-matrix.sqlite3",
        admin_token="security-matrix-admin",
    )
    with TestClient(
        create_app(settings),
        base_url="http://127.0.0.1",
        headers={"X-AI-KP-Admin-Token": "security-matrix-admin"},
    ) as client:
        campaign_a, session_a, kp_a = _campaign_session(client, "A")
        _campaign_b, session_b, kp_b = _campaign_session(client, "B")
        player_a = _join_player(client, session_a, "A player")
        player_b = _join_player(client, session_b, "B player")
        handout = client.post(
            f"/campaigns/{campaign_a['id']}/handouts",
            headers=kp_a,
            json={
                "title": "Secret clue",
                "body": "The cellar contains a ritual.",
                "kind": "clue",
                "link_type": None,
                "link_id": None,
            },
        ).json()
        opposed = client.post(
            f"/campaigns/{campaign_a['id']}/opposed-checks",
            headers=kp_a,
            json={
                "left": {"skill_name": "斗殴 A", "target": 50},
                "right": {"skill_name": "斗殴 B", "target": 50},
            },
        ).json()
        module = client.post(
            f"/campaigns/{campaign_a['id']}/modules",
            headers=kp_a,
            json={
                "title": "Security module",
                "text": "A private scene.",
                "source_type": "plaintext",
                "default_visibility": "kp",
            },
        ).json()
        run = client.post(
            f"/campaigns/{campaign_a['id']}/module-runs",
            headers=kp_a,
            json={"module_id": module["id"]},
        ).json()
        saved_map = client.post(
            f"/campaigns/{campaign_a['id']}/maps/generate",
            headers=kp_a,
            json={
                "title": "Security map",
                "prompt": "A hall and a study",
                "locations": ["Hall", "Study"],
                "routes": [["Hall", "Study"]],
            },
        ).json()
        encounter = client.post(
            f"/campaigns/{campaign_a['id']}/coc7/encounters",
            headers=kp_a,
            json={
                "kind": "combat",
                "title": "Security encounter",
                "participants": [
                    {
                        "participant_id": "keeper-a",
                        "name": "Keeper A",
                        "dex": 60,
                        "max_hp": 10,
                        "current_hp": 10,
                    },
                    {
                        "participant_id": "keeper-b",
                        "name": "Keeper B",
                        "dex": 50,
                        "max_hp": 10,
                        "current_hp": 10,
                    },
                ],
            },
        ).json()

        matrix = [
            ("anonymous_list", "get", f"/campaigns/{campaign_a['id']}/handouts", None, None, 401),
            ("player_create", "post", f"/campaigns/{campaign_a['id']}/handouts", player_a, {"title": "x", "body": "x"}, 403),
            ("player_list_own", "get", f"/campaigns/{campaign_a['id']}/handouts", player_a, None, 200),
            ("player_list_other", "get", f"/campaigns/{campaign_a['id']}/handouts", player_b, None, 404),
            ("other_kp_patch", "patch", f"/handouts/{handout['id']}", kp_b, {"expected_version": 0, "status": "revealed", "pinned": False, "link_type": None, "link_id": None}, 404),
            ("player_patch", "patch", f"/handouts/{handout['id']}", player_a, {"expected_version": 0, "status": "revealed", "pinned": False, "link_type": None, "link_id": None}, 403),
            ("player_simulations", "get", f"/campaigns/{campaign_a['id']}/simulation-cases", player_a, None, 403),
            ("other_kp_simulations", "get", f"/campaigns/{campaign_a['id']}/simulation-cases", kp_b, None, 404),
            ("player_create_opposed", "post", f"/campaigns/{campaign_a['id']}/opposed-checks", player_a, {"left": {"skill_name": "A", "target": 50}, "right": {"skill_name": "B", "target": 50}}, 403),
            ("other_kp_list_opposed", "get", f"/campaigns/{campaign_a['id']}/opposed-checks", kp_b, None, 403),
            ("other_kp_resolve_opposed", "post", f"/opposed-checks/{opposed['id']}/resolve", kp_b, None, 403),
            ("player_director_control", "post", f"/module-runs/{run['id']}/director/control", player_a, {"expected_version": run["version"], "mode": "human_kp", "reason": "unauthorized"}, 403),
            ("other_kp_director_control", "post", f"/module-runs/{run['id']}/director/control", kp_b, {"expected_version": run["version"], "mode": "human_kp", "reason": "cross campaign"}, 404),
            ("player_list_dynamic_branches", "get", f"/campaigns/{campaign_a['id']}/dynamic-branches", player_a, None, 403),
            ("other_kp_list_dynamic_branches", "get", f"/campaigns/{campaign_a['id']}/dynamic-branches", kp_b, None, 404),
            ("player_map_revision", "post", f"/maps/{saved_map['id']}/revisions", player_a, {"expected_revision_id": saved_map["revision_id"], "map_spec": saved_map["map_spec"]}, 403),
            ("other_kp_map_fog", "post", f"/maps/{saved_map['id']}/fog-regions", kp_b, {"label": "cross campaign", "polygon": [{"x": 10, "y": 10}, {"x": 100, "y": 10}, {"x": 100, "y": 100}]}, 404),
            ("player_create_encounter", "post", f"/campaigns/{campaign_a['id']}/coc7/encounters", player_a, {"kind": "combat", "title": "x", "participants": [{"participant_id": "a", "name": "A"}, {"participant_id": "b", "name": "B"}]}, 403),
            ("other_player_read_encounter", "get", f"/coc7/encounters/{encounter['id']}", player_b, None, 404),
            ("player_mutate_encounter", "post", f"/coc7/encounters/{encounter['id']}/commands", player_a, {"command_id": "unauthorized-gameplay-0001", "expected_version": 0, "command_type": "advance_turn", "payload": {}}, 403),
            ("other_kp_mutate_encounter", "post", f"/coc7/encounters/{encounter['id']}/commands", kp_b, {"command_id": "cross-campaign-gameplay-0001", "expected_version": 0, "command_type": "advance_turn", "payload": {}}, 404),
        ]
        for name, method, url, headers, payload, expected in matrix:
            response = client.request(method, url, headers=headers, json=payload)
            assert response.status_code == expected, f"{name}: {response.text}"

        # Draft content is filtered, not merely hidden by the UI.
        assert client.get(
            f"/campaigns/{campaign_a['id']}/handouts", headers=player_a
        ).json() == []
        revealed = client.patch(
            f"/handouts/{handout['id']}",
            headers=kp_a,
            json={
                "expected_version": 0,
                "status": "revealed",
                "pinned": True,
                "link_type": None,
                "link_id": None,
            },
        )
        assert revealed.status_code == 200
        player_view = client.get(
            f"/campaigns/{campaign_a['id']}/handouts", headers=player_a
        ).json()[0]
        assert player_view["body"] == "The cellar contains a ritual."
        assert "read_receipts" not in player_view
        assert "created_by_member_id" not in player_view


def test_request_transaction_rolls_back_after_injected_repository_failure(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "fault-injection.sqlite3",
        admin_token="fault-injection-admin",
    )
    app = create_app(settings)
    with TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"X-AI-KP-Admin-Token": "fault-injection-admin"},
        raise_server_exceptions=False,
    ) as client:
        campaign, _session, kp = _campaign_session(client, "Fault")

        def failing_repo() -> Iterator[Repository]:
            connection = connect(settings.db_path)
            repo = Repository(connection)
            original = repo.create_handout

            def write_then_fail(**values):
                original(**values)
                raise RuntimeError("injected after write")

            repo.create_handout = write_then_fail  # type: ignore[method-assign]
            try:
                yield repo
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

        app.dependency_overrides[get_repo] = failing_repo
        response = client.post(
            f"/campaigns/{campaign['id']}/handouts",
            headers=kp,
            json={"title": "must rollback", "body": "partial write", "kind": "clue"},
        )
        assert response.status_code == 500
        app.dependency_overrides.clear()

        connection = connect(settings.db_path)
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM campaign_handouts"
            ).fetchone()[0] == 0
        finally:
            connection.close()


def test_persisted_opposed_check_api_resolves_and_rerolls_exact_tie(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "opposed-api.sqlite3",
        admin_token="opposed-api-admin",
    )
    with TestClient(
        create_app(settings),
        base_url="http://127.0.0.1",
        headers={"X-AI-KP-Admin-Token": "opposed-api-admin"},
    ) as client:
        campaign, _session, kp = _campaign_session(client, "Opposed")

        def create_contest() -> dict:
            response = client.post(
                f"/campaigns/{campaign['id']}/opposed-checks",
                headers=kp,
                json={
                    "left": {"skill_name": "斗殴 A", "target": 50},
                    "right": {"skill_name": "斗殴 B", "target": 50},
                },
            )
            assert response.status_code == 200, response.text
            return response.json()

        contest = create_contest()
        for check_id in (contest["left_check_id"], contest["right_check_id"]):
            result = client.post(
                f"/checks/{check_id}/resolve",
                headers=kp,
                json={
                    "input_method": "physical",
                    "ones_digit": 0,
                    "tens_digits": [3],
                },
            )
            assert result.status_code == 200, result.text
        resolved = client.post(
            f"/opposed-checks/{contest['id']}/resolve", headers=kp
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["status"] == "reroll_required"
        reroll = client.post(
            f"/opposed-checks/{contest['id']}/reroll", headers=kp
        )
        assert reroll.status_code == 200, reroll.text
        assert reroll.json()["rerolled_from_opposed_check_id"] == contest["id"]
        repeated = client.post(
            f"/opposed-checks/{contest['id']}/reroll", headers=kp
        )
        assert repeated.json()["id"] == reroll.json()["id"]
