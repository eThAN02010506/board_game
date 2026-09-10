from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.campaign_objective_service import (
    CampaignObjectiveService,
    CreateObjectiveCommand,
    UpdateObjectiveCommand,
)
from ai_kp.application.errors import ConflictError
from ai_kp.application.session_continuity_service import SessionContinuityService
from ai_kp.application.session_service import SessionService
from ai_kp.core.config import Settings
from ai_kp.core.db import db_session
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db


def _identity(repo: Repository, bundle: dict):
    identity = repo.authenticate_access_token(str(bundle["access_token"]))
    assert identity is not None
    return identity


def test_session_end_continue_is_atomic_idempotent_and_role_projected(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "continuity.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Long campaign")
        session = SessionService(repo).create(str(campaign["id"]))
        kp = _identity(repo, session)
        player_bundle = SessionService(repo).join(
            str(session["join_code"]), display_name="Player"
        )
        observer_bundle = SessionService(repo).join(
            str(session["join_code"]), display_name="Observer", role="observer"
        )
        player = _identity(repo, player_bundle)
        observer = _identity(repo, observer_bundle)
        repo.activate_prepared_episode(str(session["session"]["id"]))
        continuity = SessionContinuityService(repo)
        assert continuity.view(player)["accepts_actions"] is True

        public_event = repo.append_event(
            str(campaign["id"]),
            "kp",
            "scene_progress",
            "调查员打开了钟楼大门。",
            visibility="table",
        )
        secret_event = repo.append_event(
            str(campaign["id"]),
            "kp",
            "hidden_threat",
            "地窖中的追猎者已经醒来。",
            visibility="kp",
        )
        repo.create_inventory_item(
            campaign_id=campaign["id"],
            item_type="clue",
            public_name="钟楼钥匙",
            public_description="一把公开由队伍保管的钥匙。",
            quantity=1,
            is_unique=True,
            holder_kind="party",
            holder_id=campaign["id"],
            hidden_properties={"opens": "private-vault"},
        )
        repo.set_currency_balance(
            campaign_id=campaign["id"],
            account_kind="party",
            account_id=campaign["id"],
            currency_code="USD_CENTS",
            balance_minor=250,
            expected_version=None,
        )
        objective_service = CampaignObjectiveService(repo)
        public_objective = objective_service.create(
            str(campaign["id"]),
            kp,
            CreateObjectiveCommand(
                command_id="continuity-public-objective",
                title="查明钟楼停摆原因",
                public_description="找到仍未解决的机械故障来源。",
                source_refs=({"kind": "event", "id": public_event["id"]},),
            ),
        )
        public_objective = objective_service.update(
            str(public_objective["id"]),
            kp,
            UpdateObjectiveCommand(
                command_id="continuity-block-objective",
                expected_version=int(public_objective["version"]),
                status="blocked",
                public_progress="入口已找到，但内部机关仍无法启动。",
            ),
        )
        objective_service.create(
            str(campaign["id"]),
            kp,
            CreateObjectiveCommand(
                command_id="continuity-secret-objective",
                title="追猎者倒计时",
                kp_notes="下一次钟声后推进秘密威胁。",
                visibility="kp",
            ),
        )
        npc = repo.create_npc(
            "钟楼管理员", profession="管理员", public_notes="最后见过钟表匠的人。"
        )
        repo.link_npc_to_campaign(
            str(campaign["id"]),
            str(npc["id"]),
            first_seen_time="1928-10-03 21:15",
        )
        unseen_npc = repo.create_npc(
            "尚未登场的委托人",
            profession="未知",
            public_notes="这条导入资料不应在遭遇前泄露。",
        )
        repo.link_npc_to_campaign(str(campaign["id"]), str(unseen_npc["id"]))

        ended = continuity.end(kp, client_end_id="end-session-0001")
        replay = continuity.end(kp, client_end_id="end-session-0001")
        assert ended["current_episode"]["status"] == "ended"
        assert replay["latest_snapshot"]["id"] == ended["latest_snapshot"]["id"]
        assert repo.connection.execute(
            "SELECT COUNT(*) FROM session_continuity_snapshots"
        ).fetchone()[0] == 1

        player_view = continuity.view(player)
        observer_view = continuity.view(observer)
        player_text = str(player_view)
        observer_text = str(observer_view)
        kp_text = str(continuity.view(kp))
        assert public_event["summary"] in player_text
        assert public_event["summary"] in observer_text
        assert secret_event["summary"] not in player_text
        assert secret_event["summary"] not in observer_text
        assert secret_event["summary"] in kp_text
        assert "钟楼钥匙" in player_text
        assert "钟楼钥匙" in observer_text
        assert "private-vault" not in player_text
        assert "private-vault" not in observer_text
        assert "private-vault" in kp_text
        assert "查明钟楼停摆原因" in player_text
        assert "入口已找到，但内部机关仍无法启动" in player_text
        assert "钟楼管理员" in player_text
        assert "尚未登场的委托人" not in player_text
        assert "尚未登场的委托人" not in observer_text
        assert "追猎者倒计时" not in player_text
        assert "下一次钟声后推进秘密威胁" not in player_text
        assert "追猎者倒计时" in kp_text
        assert "下一次钟声后推进秘密威胁" in kp_text
        assert "party_balances" in player_view["latest_snapshot"]["projection"]
        assert "party_balances" not in observer_view["latest_snapshot"]["projection"]
        assert "party" not in observer_view["latest_snapshot"]["projection"]
        assert "event_window_hash" not in player_view["latest_snapshot"]
        assert "event_window_hash" not in observer_view["latest_snapshot"]
        assert "event_window_hash" in continuity.view(kp)["latest_snapshot"]

        with pytest.raises(ConflictError, match="not in progress"):
            continuity.require_actions_allowed(player)

        continued = continuity.continue_campaign(
            kp, client_continue_id="continue-session-0001"
        )
        retried = continuity.continue_campaign(
            kp, client_continue_id="continue-session-0001"
        )
        assert continued["current_episode"]["sequence_no"] == 2
        assert retried["current_episode"]["id"] == continued["current_episode"]["id"]
        assert continuity.view(player)["accepts_actions"] is True

        updated = objective_service.update(
            str(public_objective["id"]),
            kp,
            UpdateObjectiveCommand(
                command_id="continuity-complete-next-episode",
                expected_version=int(public_objective["version"]),
                status="completed",
                public_progress="新 Session 中修复了机关。",
            ),
        )
        assert updated["status"] == "completed"
        assert (
            player_view["latest_snapshot"]["projection"]["current_objectives"][0]["status"]
            == "blocked"
        )

        current = continued["current_episode"]
        paused = continuity.transition(
            kp, target="paused", expected_version=int(current["version"])
        )
        assert paused["current_episode"]["status"] == "paused"
        resumed = continuity.transition(
            kp,
            target="in_progress",
            expected_version=int(paused["current_episode"]["version"]),
        )
        assert resumed["current_episode"]["status"] == "in_progress"


def test_session_end_rejects_unresolved_table_work(tmp_path: Path) -> None:
    with db_session(tmp_path / "continuity-blockers.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Blocked ending")
        session = SessionService(repo).create(str(campaign["id"]))
        kp = _identity(repo, session)
        player_bundle = SessionService(repo).join(
            str(session["join_code"]), display_name="Player"
        )
        player = _identity(repo, player_bundle)
        repo.activate_prepared_episode(str(session["session"]["id"]))
        repo.create_player_action(
            player,
            action_text="I am still deciding what to do.",
            client_action_id="continuity-pending-action",
        )

        with pytest.raises(ConflictError, match="actions=1"):
            SessionContinuityService(repo).end(kp, client_end_id="blocked-end-0001")
        assert repo.get_current_campaign_episode(kp.session_id)["status"] == "in_progress"


def test_session_end_objectives_survive_database_and_service_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "continuity-restart.sqlite3"
    connection = connect(database_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Restarted campaign")
        session = SessionService(repo).create(str(campaign["id"]))
        kp = _identity(repo, session)
        player_bundle = SessionService(repo).join(
            str(session["join_code"]), display_name="Returning Player"
        )
        player = _identity(repo, player_bundle)
        repo.activate_prepared_episode(str(session["session"]["id"]))
        objective = CampaignObjectiveService(repo).create(
            str(campaign["id"]),
            kp,
            CreateObjectiveCommand(
                command_id="restart-objective-create",
                title="Resume the interrupted investigation",
                public_description="The public objective must survive a process restart.",
            ),
        )
        CampaignObjectiveService(repo).update(
            str(objective["id"]),
            kp,
            UpdateObjectiveCommand(
                command_id="restart-objective-blocked",
                expected_version=int(objective["version"]),
                status="blocked",
                public_progress="A locked archive remains unresolved.",
            ),
        )
        ended = SessionContinuityService(repo).end(
            kp, client_end_id="restart-end-session"
        )
        snapshot_id = ended["latest_snapshot"]["id"]
        connection.commit()
        connection.close()
        connection = connect(database_path)
        init_db(connection)
        repo = Repository(connection)
        restored = SessionContinuityService(repo).view(player)
        assert restored["latest_snapshot"]["id"] == snapshot_id
        restored_objective = restored["latest_snapshot"]["projection"][
            "current_objectives"
        ][0]
        assert restored_objective["id"] == objective["id"]
        assert restored_objective["status"] == "blocked"
        assert any(
            item["public_progress"] == "A locked archive remains unresolved."
            for item in restored_objective["progress"]
        )
        continued = SessionContinuityService(repo).continue_campaign(
            kp, client_continue_id="restart-continue-session"
        )
        assert continued["current_episode"]["sequence_no"] == 2
        assert repo.connection.execute(
            "SELECT COUNT(*) FROM session_continuity_snapshots"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_latest_continuity_snapshot_uses_monotonic_insert_order_with_same_timestamp(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "continuity-order.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Fast continuity")
        session = SessionService(repo).create(str(campaign["id"]))
        kp = _identity(repo, session)
        repo.activate_prepared_episode(str(session["session"]["id"]))
        continuity = SessionContinuityService(repo)

        first = continuity.end(kp, client_end_id="fast-end-0001")
        continuity.continue_campaign(kp, client_continue_id="fast-continue-0001")
        second = continuity.end(kp, client_end_id="fast-end-0002")
        connection.execute(
            "UPDATE session_continuity_snapshots SET created_at = '2026-08-22 00:00:00'"
        )

        latest = repo.get_latest_campaign_continuity_snapshot(str(campaign["id"]))
        assert latest is not None
        assert latest["id"] == second["latest_snapshot"]["id"]
        assert latest["id"] != first["latest_snapshot"]["id"]
        assert latest["public_projection"]["episode_sequence"] == 2


def test_prepared_episode_cannot_end_before_session_zero(tmp_path: Path) -> None:
    with db_session(tmp_path / "continuity-prepared.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Prepared episode")
        session = SessionService(repo).create(str(campaign["id"]))
        kp = _identity(repo, session)

        with pytest.raises(ConflictError, match="Session 0"):
            SessionContinuityService(repo).end(kp, client_end_id="prepared-end-0001")
        assert repo.get_current_campaign_episode(kp.session_id)["status"] == "prepared"


def test_continuity_api_enforces_role_projection_and_action_gate(tmp_path: Path) -> None:
    db_path = tmp_path / "continuity-api.sqlite3"
    settings = Settings(
        db_path=db_path,
        local_admin_enabled=False,
        admin_token="continuity-admin",
    )
    with TestClient(create_app(settings)) as client:
        campaign = client.post(
            "/campaigns",
            headers={"X-AI-KP-Admin-Token": "continuity-admin"},
            json={"title": "Continuity API"},
        ).json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            headers={"X-AI-KP-Admin-Token": "continuity-admin"},
            json={"kp_display_name": "KP"},
        ).json()
        player = client.post(
            "/sessions/join",
            json={"join_code": session["join_code"], "display_name": "Player"},
        ).json()
        observer = client.post(
            "/sessions/join",
            json={
                "join_code": session["join_code"],
                "display_name": "Observer",
                "role": "observer",
            },
        ).json()
        kp_headers = {"Authorization": f"Bearer {session['access_token']}"}
        player_headers = {"Authorization": f"Bearer {player['access_token']}"}
        observer_headers = {"Authorization": f"Bearer {observer['access_token']}"}

        with db_session(db_path) as connection:
            repo = Repository(connection)
            repo.activate_prepared_episode(str(session["session"]["id"]))
            repo.append_event(
                str(campaign["id"]),
                "kp",
                "scene_progress",
                "The party opened the public gate.",
                visibility="table",
            )
            repo.append_event(
                str(campaign["id"]),
                "kp",
                "hidden_threat",
                "A private threat advanced.",
                visibility="kp",
            )

        public_objective = client.post(
            f"/campaigns/{campaign['id']}/objectives",
            headers=kp_headers,
            json={
                "command_id": "api-create-public-objective",
                "title": "Find the missing mechanism",
                "public_description": "The party still needs a safe route inside.",
                "visibility": "table",
            },
        )
        assert public_objective.status_code == 200, public_objective.text
        public_objective = public_objective.json()
        blocked_objective = client.post(
            f"/objectives/{public_objective['id']}/commands",
            headers=kp_headers,
            json={
                "command_id": "api-block-public-objective",
                "expected_version": public_objective["version"],
                "status": "blocked",
                "public_progress": "The first entrance is sealed.",
            },
        )
        assert blocked_objective.status_code == 200, blocked_objective.text
        secret_objective = client.post(
            f"/campaigns/{campaign['id']}/objectives",
            headers=kp_headers,
            json={
                "command_id": "api-create-secret-objective",
                "title": "Advance hidden threat",
                "kp_notes": "Do not reveal this objective.",
                "visibility": "kp",
            },
        )
        assert secret_objective.status_code == 200
        player_objectives = client.get(
            f"/campaigns/{campaign['id']}/objectives", headers=player_headers
        )
        assert player_objectives.status_code == 200
        assert [item["title"] for item in player_objectives.json()] == [
            "Find the missing mechanism"
        ]
        assert "Do not reveal this objective" not in player_objectives.text
        forbidden_objective = client.post(
            f"/campaigns/{campaign['id']}/objectives",
            headers=player_headers,
            json={
                "command_id": "api-player-create-objective",
                "title": "Unauthorized",
            },
        )
        assert forbidden_objective.status_code == 403

        for headers in (player_headers, observer_headers):
            forbidden = client.post(
                f"/campaigns/{campaign['id']}/session-end",
                headers=headers,
                json={"client_end_id": "forbidden-end-0001"},
            )
            assert forbidden.status_code == 403

        ended = client.post(
            f"/campaigns/{campaign['id']}/session-end",
            headers=kp_headers,
            json={"client_end_id": "continuity-api-end-0001"},
        )
        assert ended.status_code == 200, ended.text
        for headers in (player_headers, observer_headers):
            safe = client.get(
                f"/campaigns/{campaign['id']}/continuity", headers=headers
            )
            assert safe.status_code == 200
            assert "The party opened the public gate." in safe.text
            assert "A private threat advanced." not in safe.text
            assert "Find the missing mechanism" in safe.text
            assert "The first entrance is sealed." in safe.text
            assert "Advance hidden threat" not in safe.text
            assert "Do not reveal this objective" not in safe.text
            assert "event_window_hash" not in safe.json()["latest_snapshot"]
        assert "party" not in client.get(
            f"/campaigns/{campaign['id']}/continuity", headers=observer_headers
        ).json()["latest_snapshot"]["projection"]

        blocked = client.post(
            f"/campaigns/{campaign['id']}/actions",
            headers=player_headers,
            json={
                "action_text": "Try to act after Session End",
                "client_action_id": "continuity-blocked-action",
            },
        )
        assert blocked.status_code == 409

        continued = client.post(
            f"/campaigns/{campaign['id']}/continue",
            headers=kp_headers,
            json={"client_continue_id": "continuity-api-next-0001"},
        )
        assert continued.status_code == 200, continued.text
        assert continued.json()["accepts_actions"] is True
