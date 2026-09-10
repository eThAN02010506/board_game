import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.ai_control_service import AiControlService
from ai_kp.application.check_consequence_service import CheckConsequenceService
from ai_kp.application.errors import ConflictError
from ai_kp.application.fact_service import AssertWorldFactCommand, FactService
from ai_kp.application.kernel_action_service import KernelActionService
from ai_kp.application.module_run_service import DirectorControlCommand, ModuleRunService
from ai_kp.application.session_recap_service import SessionRecapService
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService
from ai_kp.bootstrap.settings import Settings
from ai_kp.director.errors import CampaignAiCallCancelledError
from ai_kp.director.orchestrator import KpOrchestrator
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.infrastructure.llm.call_registry import CampaignAiCallRegistry
from ai_kp.platform.resolution.contracts import ScenarioContract, ScenarioSnapshot


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


class ModelSwitchDuringCallLlm:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.calls = 0

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        connection = connect(self.db_path)
        try:
            Repository(connection).save_model_configuration(
                provider_type="openai_compatible",
                base_url="http://new-model.local/v1",
                api_key="replacement-secret",
                model="replacement-model",
                local_model_path=None,
                local_port=8011,
                semantic_profile="small",
            )
            connection.commit()
        finally:
            connection.close()
        return json.dumps(
            {
                "public_narration": "切换前模型的返回不得落库。",
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


class CapturingLlm:
    def __init__(self) -> None:
        self.messages = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.messages = list(messages)
        return json.dumps(
            {
                "public_narration": "AI 从已确认事实继续。",
                "kp_notes": "",
                "action_ruling": {
                    "goal": "检查侧门",
                    "method": "观察已确认的门锁状态",
                    "target": "旧仓库侧门",
                    "feasibility": "possible",
                    "resolution": "automatic",
                    "reason": "仅复述已确认事实。",
                    "maximum_effect": "告知玩家可观察状态。",
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


def test_explicit_kp_help_allows_human_control_but_not_safety_pause(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "kp-help-control.sqlite3")
    init_db(connection)
    try:
        repo = Repository(connection)
        campaign = repo.create_campaign("KP help control")
        session = SessionService(repo).create(campaign["id"])
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
        human = repo.set_module_run_control(
            run["id"],
            expected_version=run["version"],
            mode="human_kp",
            reason="Human KP requests advice explicitly",
            member_id=identity.member_id,
        )

        control = AiControlService(repo)
        snapshot = control.authorize_kp_help(campaign["id"])
        assert snapshot.allowed_modes == ("ai_assist", "human_kp")
        control.revalidate(snapshot)

        repo.set_module_run_control(
            run["id"],
            expected_version=human["version"],
            mode="safety_paused",
            reason="Stop every model call",
            member_id=identity.member_id,
        )
        with pytest.raises(ConflictError, match="safety_paused"):
            control.authorize_kp_help(campaign["id"])
        with pytest.raises(ConflictError, match="changed while human KP help"):
            control.revalidate(snapshot)
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


def test_ai_handoff_rebuilds_context_from_human_confirmed_fact(tmp_path: Path) -> None:
    connection = connect(tmp_path / "control-handoff-fact.sqlite3")
    init_db(connection)
    try:
        repo = Repository(connection)
        campaign = repo.create_campaign("人工事实交还")
        session = SessionService(repo).create(str(campaign["id"]))
        identity = repo.authenticate_access_token(str(session["access_token"]))
        assert identity is not None
        module = repo.create_module(str(campaign["id"]), "通用场景", [])
        run = repo.start_campaign_module_run(
            campaign_id=str(campaign["id"]),
            module_id=str(module["id"]),
            current_scene_key="entry",
            active_spoiler_tags=[],
            state={},
            started_by_member_id=identity.member_id,
        )
        control = ModuleRunService(repo)
        human = control.set_control(
            str(run["id"]),
            DirectorControlCommand(
                expected_version=int(run["version"]),
                mode="human_kp",
                reason="人工确认现场变化",
            ),
            member_id=identity.member_id,
        )
        FactService(repo).assert_fact(
            str(campaign["id"]),
            identity,
            AssertWorldFactCommand(
                fact_type="canonical_fact",
                subject="旧仓库侧门",
                predicate="当前状态",
                object_text="已由人类 KP 确认上锁，锁孔留有新鲜划痕。",
                source_reference={"kind": "human_kp_handoff_test"},
            ),
        )
        handed_back = control.set_control(
            str(run["id"]),
            DirectorControlCommand(
                expected_version=int(human["version"]),
                mode="ai_assist",
                reason="已记录权威事实",
            ),
            member_id=identity.member_id,
        )
        assert handed_back["director_control_mode"] == "ai_assist"

        llm = CapturingLlm()
        asyncio.run(
            KpOrchestrator(connection, llm).handle_player_action(
                str(campaign["id"]), "我查看旧仓库侧门。"
            )
        )
        prompt = "\n".join(str(message.content) for message in llm.messages)
        assert "已由人类 KP 确认上锁" in prompt
        assert "人工确认现场变化" not in prompt
    finally:
        connection.close()


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


def test_midflight_model_switch_discards_returned_model_draft(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "model-switch-race.sqlite3",
        admin_token="model-switch-admin",
        llm_base_url="http://unused.local/v1",
        llm_model="initial-model",
    )
    with TestClient(
        create_app(settings),
        base_url="http://127.0.0.1",
        headers={"X-AI-KP-Admin-Token": "model-switch-admin"},
    ) as client:
        campaign, _session, headers, _run = _setup_campaign(client)
        configured = client.put(
            "/model-settings",
            json={
                "provider_type": "openai_compatible",
                "base_url": "http://initial-model.local",
                "api_key": "initial-secret",
                "model": "initial-model",
                "semantic_profile": "small",
            },
        )
        assert configured.status_code == 200
        assert configured.json()["version"] == 1

        fake = ModelSwitchDuringCallLlm(settings.db_path)
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
        assert "Model configuration changed" in response.json()["detail"]
        assert fake.calls == 1
        connection = connect(settings.db_path)
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM turn_proposals"
            ).fetchone()[0] == 0
            configuration = Repository(connection).get_model_configuration()
            assert configuration is not None
            assert configuration["model"] == "replacement-model"
            assert configuration["version"] == 2
        finally:
            connection.close()


def test_every_game_director_model_entry_uses_the_same_control_gate() -> None:
    methods = (
        TurnService.create_ai_proposal,
        TurnService.create_world_expansion_proposal,
        CheckConsequenceService.generate,
        SessionRecapService.generate,
        KernelActionService.prepare,
    )
    for method in methods:
        source = inspect.getsource(method)
        assert "AiControlService(self.repo)" in source, method.__qualname__
        assert ".authorize" in source, method.__qualname__
        assert ".revalidate" in source, method.__qualname__


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


def test_kernel_semantic_call_uses_campaign_cancellation_registry() -> None:
    class BlockingLlm:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def complete(self, messages, temperature: float = 0.7) -> str:
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def exercise() -> None:
        registry = CampaignAiCallRegistry()
        llm = BlockingLlm()
        director = KpOrchestrator(None, llm, call_registry=registry)  # type: ignore[arg-type]
        contract = ScenarioContract.model_validate(
            {
                "contract_id": "control-gate",
                "source_version": 1,
                "ruleset_id": "coc7",
                "title": "Control gate fixture",
                "operators": [
                    {
                        "operator_id": "wait",
                        "title": "Wait",
                        "policy": "automatic",
                        "success_commands": [
                            {"kind": "emit_event", "event_type": "waited"}
                        ],
                    }
                ],
            }
        )
        task = asyncio.create_task(
            director.select_kernel_action(
                campaign_id="campaign_kernel",
                contract=contract,
                snapshot=ScenarioSnapshot(
                    run_id="run-1",
                    contract_id=contract.contract_id,
                    scenario_version=1,
                    run_version=0,
                ),
                player_action="wait",
                profile="small",
            )
        )
        await llm.started.wait()
        assert registry.active_count("campaign_kernel") == 1
        assert registry.cancel_campaign("campaign_kernel") == 1
        with pytest.raises(CampaignAiCallCancelledError):
            await task
        assert registry.active_count("campaign_kernel") == 0

    asyncio.run(exercise())
