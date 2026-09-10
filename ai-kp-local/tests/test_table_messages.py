from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.session_service import SessionService
from ai_kp.application.table_message_service import TableMessageService
from ai_kp.core.config import Settings
from ai_kp.core.db import db_session
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.session_zero import CampaignSetupConfig


def _identity(repo: Repository, bundle: dict):
    identity = repo.authenticate_access_token(str(bundle["access_token"]))
    assert identity is not None
    return identity


def test_message_visibility_and_whisper_policy_are_deterministic(tmp_path: Path) -> None:
    with db_session(tmp_path / "messages.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Message table")
        session = SessionService(repo).create(str(campaign["id"]))
        first = SessionService(repo).join(str(session["join_code"]), display_name="First")
        second = SessionService(repo).join(str(session["join_code"]), display_name="Second")
        observer = SessionService(repo).join(
            str(session["join_code"]), display_name="Watcher", role="observer"
        )
        kp = _identity(repo, session)
        first_identity = _identity(repo, first)
        second_identity = _identity(repo, second)
        observer_identity = _identity(repo, observer)
        service = TableMessageService(repo)

        table = service.send(
            first_identity,
            audience="table",
            content="  We enter the hall.  ",
            recipient_member_id=None,
            client_message_id="table-message-0001",
        )
        party = service.send(
            second_identity,
            audience="party",
            content="Keep the key hidden.",
            recipient_member_id=None,
            client_message_id="party-message-0001",
        )
        direct = service.send(
            first_identity,
            audience="direct",
            content="A private question for the KP.",
            recipient_member_id=kp.member_id,
            client_message_id="direct-message-0001",
        )
        service.send(
            kp,
            audience="announcement",
            content="Five minutes remain.",
            recipient_member_id=None,
            client_message_id="announce-message-1",
        )

        assert table["content"] == "We enter the hall."
        observer_messages = service.list_messages(
            observer_identity, before_id=None, limit=20
        )
        assert [item["audience"] for item in observer_messages] == [
            "table",
            "announcement",
        ]
        observer_text = str(observer_messages)
        assert "Five minutes remain." in observer_text
        assert party["content"] not in observer_text
        assert direct["content"] not in observer_text
        assert direct["content"] in str(service.list_messages(kp, before_id=None, limit=20))
        assert direct["content"] not in str(
            service.list_messages(second_identity, before_id=None, limit=20)
        )

        late = SessionService(repo).join(
            str(session["join_code"]), display_name="Late player"
        )
        late_identity = _identity(repo, late)
        late_history = str(service.list_messages(late_identity, before_id=None, limit=20))
        assert "We enter the hall." in late_history
        assert "Five minutes remain." in late_history
        assert party["content"] not in late_history
        service.send(
            second_identity,
            audience="party",
            content="New party information after the player joined.",
            recipient_member_id=None,
            client_message_id="party-message-0002",
        )
        assert "New party information after the player joined." in str(
            service.list_messages(late_identity, before_id=None, limit=20)
        )

        with pytest.raises(PermissionError, match="read-only"):
            service.send(
                observer_identity,
                audience="table",
                content="Observers cannot talk over the table.",
                recipient_member_id=None,
                client_message_id="observer-table-001",
            )
        observer_direct = service.send(
            observer_identity,
            audience="direct",
            content="Private note to the KP.",
            recipient_member_id=kp.member_id,
            client_message_id="observer-direct-01",
        )
        assert observer_direct["recipient"]["role"] == "kp"

        with pytest.raises(PermissionError, match="disabled"):
            service.send(
                first_identity,
                audience="direct",
                content="Player whisper before consent policy allows it.",
                recipient_member_id=second_identity.member_id,
                client_message_id="player-whisper-001",
            )
        repo.create_session_zero_revision(
            campaign_id=str(campaign["id"]),
            config=CampaignSetupConfig(
                ruleset_id=str(campaign["ruleset_id"]),
                ruleset_version=str(campaign["ruleset_version"]),
                allow_player_whispers=True,
            ),
            expected_version=0,
            actor_member_id=kp.member_id,
        )
        allowed = service.send(
            first_identity,
            audience="direct",
            content="Player whisper after the table policy allows it.",
            recipient_member_id=second_identity.member_id,
            client_message_id="player-whisper-002",
        )
        assert allowed["recipient"]["member_id"] == second_identity.member_id

        replay = service.send(
            first_identity,
            audience="table",
            content="We enter the hall.",
            recipient_member_id=None,
            client_message_id="table-message-0001",
        )
        assert replay["id"] == table["id"]
        with pytest.raises(ValueError, match="different content"):
            service.send(
                first_identity,
                audience="table",
                content="Changed replay content.",
                recipient_member_id=None,
                client_message_id="table-message-0001",
            )


def test_observer_api_is_read_only_and_does_not_block_session_zero(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "observer-api.sqlite3",
        local_admin_enabled=False,
        admin_token="observer-test-admin",
    )
    admin_headers = {"X-AI-KP-Admin-Token": "observer-test-admin"}
    with TestClient(create_app(settings)) as client:
        campaign = client.post(
            "/campaigns", headers=admin_headers, json={"title": "Observer API"}
        ).json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            headers=admin_headers,
            json={"kp_display_name": "KP"},
        ).json()
        kp_headers = {"Authorization": f"Bearer {session['access_token']}"}
        joined = client.post(
            "/sessions/join",
            json={
                "join_code": session["join_code"],
                "display_name": "Observer",
                "role": "observer",
            },
        )
        assert joined.status_code == 200, joined.text
        observer = joined.json()
        observer_headers = {"Authorization": f"Bearer {observer['access_token']}"}
        assert observer["member"]["role"] == "observer"
        assert "private_history_from_sequence" not in observer["member"]

        view = client.get(
            f"/campaigns/{campaign['id']}/session-zero", headers=kp_headers
        ).json()
        assert view["ready"] is True
        assert view["required_count"] == 1

        assert client.get(
            f"/campaigns/{campaign['id']}/public-turns", headers=observer_headers
        ).status_code == 200
        assert client.get(
            f"/campaigns/{campaign['id']}/module-runs/play-state",
            headers=observer_headers,
        ).status_code == 200
        assert client.get(
            f"/campaigns/{campaign['id']}/handouts", headers=observer_headers
        ).status_code == 200
        assert client.get(
            f"/campaigns/{campaign['id']}/proposals", headers=observer_headers
        ).status_code == 403
        assert client.post(
            f"/campaigns/{campaign['id']}/actions",
            headers=observer_headers,
            json={"action_text": "I act", "client_action_id": "observer-action-001"},
        ).status_code == 403

        sent = client.post(
            f"/campaigns/{campaign['id']}/messages",
            headers=kp_headers,
            json={
                "audience": "announcement",
                "content": "Welcome, observers.",
                "recipient_member_id": None,
                "client_message_id": "observer-api-message-1",
            },
        )
        assert sent.status_code == 200, sent.text
        visible = client.get(
            f"/campaigns/{campaign['id']}/messages", headers=observer_headers
        )
        assert visible.status_code == 200
        assert [item["content"] for item in visible.json()] == ["Welcome, observers."]
        denied = client.post(
            f"/campaigns/{campaign['id']}/messages",
            headers=observer_headers,
            json={
                "audience": "party",
                "content": "Must not be sent.",
                "recipient_member_id": None,
                "client_message_id": "observer-api-message-2",
            },
        )
        assert denied.status_code == 403


def test_observer_realtime_projection_is_an_explicit_allowlist(tmp_path: Path) -> None:
    with db_session(tmp_path / "observer-realtime.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Observer realtime")
        session = SessionService(repo).create(str(campaign["id"]))
        observer = SessionService(repo).join(
            str(session["join_code"]), display_name="Watcher", role="observer"
        )
        identity = _identity(repo, observer)

        repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=identity.campaign_id,
            audience="session",
            event_type="session.member_joined",
            payload={"secret_admin_data": "must-not-leak"},
        )
        repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=identity.campaign_id,
            audience="session",
            event_type="world.updated",
            payload={"version": 2},
        )
        repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=identity.campaign_id,
            audience="member",
            member_id=identity.member_id,
            event_type="table_message.created",
            payload={"message_id": "observer-private-note"},
        )

        visible = repo.list_visible_realtime_events(
            session_id=identity.session_id,
            role=identity.role,
            member_id=identity.member_id,
        )
        assert [event["event_type"] for event in visible] == [
            "world.updated",
            "table_message.created",
        ]
        assert "secret_admin_data" not in str(visible)
