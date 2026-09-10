from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.session_service import SessionService
from ai_kp.bootstrap.settings import Settings
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository


def _bearer(bundle: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {bundle['access_token']}"}


def _seed_campaign(repo: Repository, title: str) -> dict[str, Any]:
    campaign = repo.create_campaign(title)
    session = SessionService(repo).create(
        campaign["id"],
        kp_display_name=f"{title} KP",
    )
    player = SessionService(repo).join(
        session["join_code"],
        display_name=f"{title} Player",
        role="player",
    )
    observer = SessionService(repo).join(
        session["join_code"],
        display_name=f"{title} Observer",
        role="observer",
    )
    module = repo.create_module(campaign["id"], f"{title} module", [])
    run = repo.start_campaign_module_run(
        campaign_id=campaign["id"],
        module_id=module["id"],
        current_scene_key=None,
        active_spoiler_tags=[],
        state={},
        started_by_member_id=session["member"]["id"],
    )
    return {
        "campaign": campaign,
        "session": session,
        "player": player,
        "observer": observer,
        "run": run,
    }


def _append_requested(
    repo: Repository,
    seeded: dict[str, Any],
    attempt_id: str,
    *,
    requested_by_member_id: str | None,
) -> None:
    repo.append_director_help_audit_event(
        attempt_id=attempt_id,
        campaign_id=seeded["campaign"]["id"],
        run_id=seeded["run"]["id"],
        requested_by_member_id=requested_by_member_id,
        request_id=f"request-{attempt_id}",
        event_type="requested",
        question=f"Secret question for {attempt_id}",
    )


def _history_endpoint(seeded: dict[str, Any]) -> str:
    return f"/campaigns/{seeded['campaign']['id']}/director-help/audits"


def test_history_requires_the_same_campaign_kp_role(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "director-help-history-auth.sqlite3",
        module_asset_root=tmp_path / "module-assets",
        local_admin_enabled=False,
    )
    app = create_app(settings)
    with db_session(settings.db_path) as connection:
        repo = Repository(connection)
        campaign = _seed_campaign(repo, "History A")
        other_campaign = _seed_campaign(repo, "History B")
        _append_requested(
            repo,
            campaign,
            "auth-attempt",
            requested_by_member_id=campaign["session"]["member"]["id"],
        )

    endpoint = _history_endpoint(campaign)
    with TestClient(app) as client:
        unauthenticated = client.get(endpoint)
        player = client.get(endpoint, headers=_bearer(campaign["player"]))
        observer = client.get(endpoint, headers=_bearer(campaign["observer"]))
        foreign_kp = client.get(
            endpoint,
            headers=_bearer(other_campaign["session"]),
        )
        own_kp = client.get(endpoint, headers=_bearer(campaign["session"]))

    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {"detail": "Session token required"}
    for response in (player, observer):
        assert response.status_code == 403
        assert response.json() == {"detail": "Required role: kp"}
    assert foreign_kp.status_code == 404
    assert foreign_kp.json() == {"detail": "Campaign resource not found"}
    assert own_kp.status_code == 200
    assert [item["id"] for item in own_kp.json()["items"]] == ["auth-attempt"]


def test_history_cursor_is_campaign_scoped_and_opaque(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "director-help-history-cursor.sqlite3",
        module_asset_root=tmp_path / "module-assets",
        local_admin_enabled=False,
    )
    app = create_app(settings)
    with db_session(settings.db_path) as connection:
        repo = Repository(connection)
        first = _seed_campaign(repo, "Cursor A")
        second = _seed_campaign(repo, "Cursor B")
        _append_requested(
            repo,
            first,
            "foreign-secret-attempt",
            requested_by_member_id=first["session"]["member"]["id"],
        )
        _append_requested(
            repo,
            second,
            "local-attempt",
            requested_by_member_id=second["session"]["member"]["id"],
        )

    endpoint = _history_endpoint(second)
    headers = _bearer(second["session"])
    with TestClient(app) as client:
        cross_campaign = client.get(
            endpoint,
            headers=headers,
            params={"before_id": "foreign-secret-attempt"},
        )
        nonexistent = client.get(
            endpoint,
            headers=headers,
            params={"before_id": "missing-attempt"},
        )

    for response in (cross_campaign, nonexistent):
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
        serialized = response.text
        assert first["campaign"]["id"] not in serialized
        assert first["run"]["id"] not in serialized
        assert "Secret question for foreign-secret-attempt" not in serialized
        assert "items" not in response.json()


def test_history_pages_by_requested_sequence_and_allows_null_requesting_member(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "director-help-history-pages.sqlite3",
        module_asset_root=tmp_path / "module-assets",
        local_admin_enabled=False,
    )
    app = create_app(settings)
    with db_session(settings.db_path) as connection:
        repo = Repository(connection)
        seeded = _seed_campaign(repo, "Pagination")
        kp_member_id = seeded["session"]["member"]["id"]
        _append_requested(
            repo,
            seeded,
            "page-attempt-1",
            requested_by_member_id=kp_member_id,
        )
        _append_requested(
            repo,
            seeded,
            "page-attempt-2",
            requested_by_member_id=None,
        )
        _append_requested(
            repo,
            seeded,
            "page-attempt-3",
            requested_by_member_id=kp_member_id,
        )
        connection.execute(
            """
            UPDATE director_help_audit_events
            SET created_at = '2026-08-01 12:00:00'
            WHERE campaign_id = ? AND event_type = 'requested'
            """,
            (seeded["campaign"]["id"],),
        )

    endpoint = _history_endpoint(seeded)
    headers = _bearer(seeded["session"])
    with TestClient(app) as client:
        first_response = client.get(
            endpoint,
            headers=headers,
            params={"limit": 2},
        )
        assert first_response.status_code == 200
        first_page = first_response.json()
        second_response = client.get(
            endpoint,
            headers=headers,
            params={
                "limit": 2,
                "before_id": first_page["next_before_id"],
            },
        )

    assert second_response.status_code == 200
    second_page = second_response.json()
    assert [item["id"] for item in first_page["items"]] == [
        "page-attempt-3",
        "page-attempt-2",
    ]
    assert first_page["next_before_id"] == "page-attempt-2"
    assert first_page["items"][1]["requested_by_member_id"] is None
    assert [item["created_at"] for item in first_page["items"]] == [
        "2026-08-01 12:00:00",
        "2026-08-01 12:00:00",
    ]
    assert [item["id"] for item in second_page["items"]] == ["page-attempt-1"]
    assert second_page["next_before_id"] is None
    first_ids = {item["id"] for item in first_page["items"]}
    second_ids = {item["id"] for item in second_page["items"]}
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == {
        "page-attempt-1",
        "page-attempt-2",
        "page-attempt-3",
    }
