import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.ai_control_service import AiControlService
from ai_kp.application.check_consequence_service import CheckConsequenceService
from ai_kp.application.errors import ConflictError
from ai_kp.application.session_recap_service import SessionRecapService
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.infrastructure.llm.call_registry import CampaignAiCallRegistry


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _setup_campaign(client: TestClient) -> tuple[dict, dict, dict, dict]:
    campaign = client.post("/campaigns", json={"title": "AI 控制测试"}).json()
    session = client.post(
        f"/campaigns/{campaign['id']}/sessions",
        json={"kp_display_name": "OldOnes"},
    ).json()
    headers = _headers(session["access_token"])
    module = client.post(
        f"/campaigns/{campaign['id']}/modules",
        headers=headers,
        json={
            "title": "控制测试模组",
            "text": "钟楼是当前唯一确定的地点。",
            "source_type": "plaintext",
            "default_visibility": "kp",
        },
    ).json()
    run = client.post(
        f"/campaigns/{campaign['id']}/module-runs",
        headers=headers,
        json={"module_id": module["id"], "current_scene_key": "town"},
    ).json()
    return campaign, session, headers, run


class CountingLlm:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        raise AssertionError("blocked AI operations must not reach the model")


class HandoffDuringCallLlm:
    def __init__(self, db_path: Path, run_id: str, access_token: str) -> None:
        self.db_path = db_path
        self.run_id = run_id
        self.access_token = access_token
        self.calls = 0

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            identity = repo.authenticate_access_token(self.access_token)
            assert identity is not None
            run = repo.get_campaign_module_run(self.run_id)
            repo.set_module_run_control(
                self.run_id,
                expected_version=run["version"],
                mode="human_kp",
                reason="模型调用期间紧急接管",
                member_id=identity.member_id,
            )
            connection.commit()
        finally:
            connection.close()
        return json.dumps(
            {
                "public_narration": "这段输出不得落库。",
                "kp_notes": "",
                "action_ruling": {
                    "goal": "推进当前行动",
                    "method": "观察",
                    "target": "当前场景",
                    "feasibility": "possible",
                    "resolution": "automatic",
                    "reason": "测试输出。",
                    "maximum_effect": "仅限公开叙述。",
                    "alternative": "",
                },
                "proposed_checks": [],
                "proposed_events": [],
                "proposed_memories": [],
                "proposed_npc_updates": [],
                "proposed_map_moves": [],
                "proposed_facts": [],
            },
            ensure_ascii=False,
        )


def test_control_snapshot_fails_closed_when_run_changes(tmp_path: Path) -> None:
    connection = connect(tmp_path / "control-service.sqlite3")
    init_db(connection)
    try:
        repo = Repository(connection)
        campaign = repo.create_campaign("控制快照")
        session = SessionService(repo).create(
            campaign["id"],
            kp_display_name="OldOnes",
        )
        identity = repo.authenticate_access_token(session["access_token"])
        assert identity is not None
        module = repo.create_module(campaign["id"], "模组", [])
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key="town",
            active_spoiler_tags=[],
            state={},
            started_by_member_id=identity.member_id,
        )
        snapshot = AiControlService(repo).authorize(campaign["id"], "test call")
        repo.set_module_run_control(
            run["id"],
            expected_version=run["version"],
            mode="safety_paused",
            reason="测试暂停",
            member_id=identity.member_id,
        )
        with patch.object(
            repo,
            "get_active_campaign_module_run",
            wraps=repo.get_active_campaign_module_run,
        ):
            try:
                AiControlService(repo).revalidate(snapshot)
            except ConflictError as exc:
                assert "changed while test call was running" in str(exc)
            else:
                raise AssertionError("stale AI control snapshot was accepted")
        with patch.object(
            repo,
            "get_active_campaign_module_run",
            wraps=repo.get_active_campaign_module_run,
        ):
            try:
                AiControlService(repo).authorize(campaign["id"], "test call")
            except ConflictError as exc:
                assert "safety_paused" in str(exc)
            else:
                raise AssertionError("paused campaign was authorized")
    finally:
        connection.close()


def test_human_takeover_blocks_all_game_director_entrypoints(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "control-api.sqlite3",
        admin_token="control-admin",
        llm_base_url="http://unused.local/v1",
        llm_model="blocked-model",
    )
    with TestClient(
        create_app(settings),
        base_url="http://127.0.0.1",
        headers={"X-AI-KP-Admin-Token": "control-admin"},
    ) as client:
        campaign, session, headers, run = _setup_campaign(client)
        event = client.post(
            f"/campaigns/{campaign['id']}/events",
            headers=headers,
            json={
                "actor_type": "kp",
                "event_type": "scene.started",
                "summary": "调查开始。",
                "visibility": "table",
            },
        )
        assert event.status_code == 200
        takeover = client.post(
            f"/module-runs/{run['id']}/director/control",
            headers=headers,
            json={
                "expected_version": run["version"],
                "mode": "human_kp",
                "reason": "测试完全接管",
            },
        )
        assert takeover.status_code == 200
        fake = CountingLlm()
        with patch("ai_kp.api.main.OpenAICompatibleClient", return_value=fake):
            requests = (
                client.post(
                    "/kp/turn",
                    headers=headers,
                    json={
                        "campaign_id": campaign["id"],
                        "player_action": "检查钟楼。",
                    },
                ),
                client.post(
                    f"/sessions/{session['session']['id']}/recaps/generate",
                    headers=headers,
                ),
                client.post(
                    f"/module-runs/{run['id']}/director/world-expansion-proposals",
                    headers=headers,
                    json={"player_intent": "寻找镇上的治安官"},
                ),
            )
        assert [response.status_code for response in requests] == [409, 409, 409]
        assert fake.calls == 0


def test_midflight_human_takeover_discards_returned_model_draft(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "control-race.sqlite3",
        admin_token="control-race-admin",
        llm_base_url="http://unused.local/v1",
        llm_model="race-model",
    )
    with TestClient(
        create_app(settings),
        base_url="http://127.0.0.1",
        headers={"X-AI-KP-Admin-Token": "control-race-admin"},
    ) as client:
        campaign, session, headers, run = _setup_campaign(client)
        fake = HandoffDuringCallLlm(
            settings.db_path,
            run["id"],
            session["access_token"],
        )
        with patch("ai_kp.api.main.OpenAICompatibleClient", return_value=fake):
            response = client.post(
                "/kp/turn",
                headers=headers,
                json={
                    "campaign_id": campaign["id"],
                    "player_action": "检查钟楼。",
                },
            )
        assert response.status_code == 409, response.text
        assert fake.calls == 1
        connection = connect(settings.db_path)
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM turn_proposals"
            ).fetchone()[0] == 0
            mode = connection.execute(
                """
                SELECT director_control_mode FROM campaign_module_runs
                WHERE id = ?
                """,
                (run["id"],),
            ).fetchone()[0]
            assert mode == "human_kp"
        finally:
            connection.close()


def test_every_game_director_model_entry_uses_the_same_control_gate() -> None:
    methods = (
        TurnService.create_ai_proposal,
        TurnService.create_world_expansion_proposal,
        CheckConsequenceService.generate,
        SessionRecapService.generate,
    )
    for method in methods:
        source = inspect.getsource(method)
        assert "AiControlService(self.repo).authorize" in source, method.__qualname__
        assert "AiControlService(self.repo).revalidate" in source, method.__qualname__


def test_campaign_call_registry_cancels_and_releases_inflight_task() -> None:
    async def exercise() -> None:
        registry = CampaignAiCallRegistry()
        started = asyncio.Event()

        async def model_call() -> None:
            with registry.track("campaign_1"):
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(model_call())
        await started.wait()
        assert registry.active_count("campaign_1") == 1
        assert registry.cancel_campaign("campaign_1") == 1
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("registered model call was not cancelled")
        assert registry.active_count("campaign_1") == 0

    asyncio.run(exercise())
