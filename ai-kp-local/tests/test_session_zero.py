from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.session_service import SessionService
from ai_kp.application.session_zero_service import SessionZeroService
from ai_kp.core.config import Settings
from ai_kp.core.db import db_session
from ai_kp.director.session_safety_policy import CampaignSafetyPolicyLlm
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.ports.llm import ChatMessage
from ai_kp.platform.sessions.session_zero import (
    CampaignSetupConfig,
    SessionZeroPreferences,
)
from tests.test_parallel_kernel_service import setup_bound_run


class RecordingLlm:
    def __init__(self) -> None:
        self.messages: list[list[ChatMessage]] = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.messages.append(messages)
        return '{"ok":true}'


def _config(campaign: dict) -> CampaignSetupConfig:
    return CampaignSetupConfig(
        ruleset_id=str(campaign["ruleset_id"]),
        ruleset_version=str(campaign["ruleset_version"]),
        worldview="A long-running mystery campaign",
        hosting_mode="ai_kp",
        expected_player_count=2,
        style={"roleplay": 4, "exploration": 4, "combat": 2, "tone": "mixed"},
        content_warnings=("body horror",),
        lines=("sexual violence",),
        veils=("medical detail",),
        idle_policy="defend",
    )


def test_session_zero_revisions_require_fresh_table_consent_and_hide_owners(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "session-zero.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Session Zero campaign")
        session = SessionService(repo).create(str(campaign["id"]))
        kp = repo.authenticate_access_token(str(session["access_token"]))
        assert kp is not None
        service = SessionZeroService(repo)

        kp_view = service.save_config(kp, expected_version=0, config=_config(campaign))
        assert kp_view["ready"] is True
        assert kp_view["revision"]["status"] == "active"

        first_bundle = SessionService(repo).join(
            str(session["join_code"]), display_name="First"
        )
        second_bundle = SessionService(repo).join(
            str(session["join_code"]), display_name="Second"
        )
        first = repo.authenticate_access_token(str(first_bundle["access_token"]))
        second = repo.authenticate_access_token(str(second_bundle["access_token"]))
        assert first is not None and second is not None
        assert service.view(first)["ready"] is False

        first_view = service.save_preferences(
            first,
            expected_version=1,
            preferences=SessionZeroPreferences(
                public_style={"humor": "occasional"},
                private_style={"horror": "low"},
                lines=("private line",),
                veils=("private veil",),
            ),
        )
        revision = first_view["revision"]
        assert revision["version"] == 2
        assert revision["status"] == "pending"
        assert first_view["confirmed"] is True
        assert first_view["own_preferences"]["lines"] == ["private line"]

        second_view = service.view(second)
        assert second_view["own_preferences"] is None
        assert "private_style" not in str(second_view["public_preferences"])
        assert repo.session_zero_model_policy(str(campaign["id"])).lines == (
            "sexual violence",
            "private line",
        )
        assert second_view["model_policy"] is None
        assert "private line" not in str(second_view)

        # The prior active agreement cannot be accepted after a new pending
        # revision becomes the table's current consent target.
        with pytest.raises(ValueError, match="changed"):
            service.confirm(
                second,
                revision_id=str(kp_view["revision"]["id"]),
                expected_version=1,
            )

        service.confirm(
            second,
            revision_id=str(revision["id"]),
            expected_version=2,
        )
        final = service.confirm(
            kp,
            revision_id=str(revision["id"]),
            expected_version=2,
        )
        assert final["ready"] is True
        assert final["revision"]["status"] == "active"
        assert final["confirmed_count"] == 3


def test_safety_tool_records_no_reason_and_pauses_then_resumes_ai(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "session-safety.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)
        session = SessionService(repo).create(str(run["campaign_id"]))
        player_bundle = SessionService(repo).join(
            str(session["join_code"]), display_name="Player"
        )
        player = repo.authenticate_access_token(str(player_bundle["access_token"]))
        kp = repo.authenticate_access_token(str(session["access_token"]))
        assert player is not None and kp is not None
        service = SessionZeroService(repo)

        triggered = service.trigger_safety(player, response_kind="change")

        event = triggered["event"]
        assert set(event) == {
            "id",
            "status",
            "response_kind",
            "public_message",
            "created_at",
            "resolved_at",
            "resolution_kind",
        }
        stored = repo.get_session_safety_event(str(event["id"]))
        assert "reason" not in stored
        assert repo.get_campaign_module_run(str(run["id"]))["director_control_mode"] == "safety_paused"
        kp_view = service.view(kp)
        assert "actor_member_id" not in kp_view["active_safety_event"]

        resolved = service.resolve_safety(
            player,
            event_id=str(event["id"]),
            resolution_kind="change",
        )
        assert resolved["event"]["status"] == "resolved"
        assert repo.get_campaign_module_run(str(run["id"]))["director_control_mode"] == "ai_assist"


def test_generation_policy_is_anonymous_and_bound_to_campaign_scope(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "session-zero-policy.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Policy campaign")
        session = SessionService(repo).create(str(campaign["id"]))
        kp = repo.authenticate_access_token(str(session["access_token"]))
        assert kp is not None
        SessionZeroService(repo).save_config(
            kp, expected_version=0, config=_config(campaign)
        )
        inner = RecordingLlm()
        guarded = CampaignSafetyPolicyLlm(connection, inner)

        with guarded.bind_campaign(str(campaign["id"])):
            asyncio.run(
                guarded.complete([ChatMessage(role="user", content="Continue scene")])
            )

        policy_prompt = inner.messages[0][0].content
        assert "sexual violence" in policy_prompt
        assert "medical detail" in policy_prompt
        assert kp.member_id not in policy_prompt
        assert "不得猜测" in policy_prompt


def test_session_zero_api_rejects_reason_collection_and_cross_member_private_style(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "session-zero-api.sqlite3",
        local_admin_enabled=False,
        admin_token="session-zero-admin",
    )
    with TestClient(create_app(settings)) as client:
        campaign_response = client.post(
            "/campaigns",
            headers={"X-AI-KP-Admin-Token": "session-zero-admin"},
            json={"title": "API Session Zero"},
        )
        assert campaign_response.status_code == 200, campaign_response.text
        campaign = campaign_response.json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            headers={"X-AI-KP-Admin-Token": "session-zero-admin"},
            json={"kp_display_name": "KP"},
        ).json()
        kp_headers = {"Authorization": f"Bearer {session['access_token']}"}
        config = _config(campaign).model_dump(mode="json")
        saved = client.put(
            f"/campaigns/{campaign['id']}/session-zero/config",
            headers=kp_headers,
            json={"expected_version": 1, **config},
        )
        assert saved.status_code == 200, saved.text
        first = client.post(
            "/sessions/join",
            json={"join_code": session["join_code"], "display_name": "First"},
        ).json()
        second = client.post(
            "/sessions/join",
            json={"join_code": session["join_code"], "display_name": "Second"},
        ).json()
        first_headers = {"Authorization": f"Bearer {first['access_token']}"}
        second_headers = {"Authorization": f"Bearer {second['access_token']}"}

        blocked_play = client.post(
            f"/campaigns/{campaign['id']}/actions",
            headers=first_headers,
            json={
                "action_text": "Attempt play before Session 0 consent",
                "client_action_id": "session-zero-gate-action",
            },
        )
        assert blocked_play.status_code == 409
        assert "Session 0" in blocked_play.json()["detail"]

        preference = client.put(
            f"/campaigns/{campaign['id']}/session-zero/preferences",
            headers=first_headers,
            json={
                "expected_version": 2,
                "public_style": {},
                "private_style": {"notes": "only the owner may read this"},
                "lines": ["anonymous boundary"],
                "veils": [],
            },
        )
        assert preference.status_code == 200, preference.text
        other = client.get(
            f"/campaigns/{campaign['id']}/session-zero", headers=second_headers
        )
        assert other.status_code == 200
        assert "only the owner" not in other.text
        assert other.json()["model_policy"] is None
        assert "anonymous boundary" not in other.text
        assert first["member"]["id"] not in other.text

        rejected_reason = client.post(
            f"/campaigns/{campaign['id']}/safety-tool",
            headers=first_headers,
            json={"response_kind": "pause", "reason": "must never be stored"},
        )
        assert rejected_reason.status_code == 422
        triggered = client.post(
            f"/campaigns/{campaign['id']}/safety-tool",
            headers=first_headers,
            json={"response_kind": "pause"},
        )
        assert triggered.status_code == 200
        assert "actor_member_id" not in triggered.text
