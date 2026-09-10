from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.api.routers import module_runs as module_runs_router
from ai_kp.application.director_help_service import DirectorHelpService
from ai_kp.application.errors import (
    ConflictError,
    DirectorHelpAuditUnavailableError,
    DirectorHelpClientDisconnectedError,
)
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.application.session_service import SessionService
from ai_kp.bootstrap.settings import Settings
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.director.errors import CampaignAiCallCancelledError
from ai_kp.director.human_kp_help import DirectorHelpOutput
from ai_kp.director.turn_output import StructuredOutputError
from ai_kp.infrastructure.llm.director_help_call_gate import DirectorHelpCallGate
from ai_kp.platform.modules.ingestion import ModuleChunk
from ai_kp.platform.resolution.json_projection import canonical_json_bytes
from tests.scenario_contract_testkit import bind_payload_to_module

_CONTRACT_FIXTURE = (
    Path(__file__).parent / "fixtures" / "scenario_contracts" / "open_investigation.json"
)

# These tables collectively hold the authoritative run, scenario, fact/event,
# proposal, adjudication, and realtime projections that a display-only help call
# must never touch. Comparing full rows also catches updates with unchanged counts.
_GAMEPLAY_TABLES = (
    "campaign_module_runs",
    "scenario_run_states",
    "scenario_command_batches",
    "scenario_contract_overlays",
    "kernel_plan_instances",
    "action_resolution_previews",
    "events",
    "turn_proposals",
    "proposal_actions",
    "player_actions",
    "player_action_adjudications",
    "player_action_adjudication_events",
    "module_run_scene_events",
    "module_run_entity_states",
    "module_run_entity_state_events",
    "parallel_action_batches",
    "parallel_action_batch_items",
    "parallel_action_batch_events",
    "dynamic_branch_runs",
    "dynamic_branch_events",
    "skill_checks",
    "skill_check_actions",
    "memories",
    "realtime_events",
)


class FakeHelpDirector:
    def __init__(self, repo: Repository | None = None, run_id: str | None = None):
        self.repo = repo
        self.run_id = run_id
        self.calls = 0
        self.request: dict[str, Any] | None = None

    async def advise_human_kp(
        self,
        *,
        campaign_id: str,
        brief: dict[str, Any],
    ) -> DirectorHelpOutput:
        assert campaign_id
        self.calls += 1
        self.request = brief
        return DirectorHelpOutput(
            status="answered",
            answer="The published contract offers an archive search.",
            suggested_response="Ask for Library Use; success identifies the subject.",
            candidate_id="search-archive",
            requested_skill_key="library_use",
            next_steps=["Describe the archive before requesting the check."],
            evidence_ids=["operator:search-archive"],
            confidence="high",
            uncertainty_reasons=[],
            assumptions=[],
            follow_up_question=None,
        )


class StateChangingHelpDirector(FakeHelpDirector):
    async def advise_human_kp(
        self,
        *,
        campaign_id: str,
        brief: dict[str, Any],
    ) -> DirectorHelpOutput:
        assert self.repo is not None and self.run_id is not None
        output = await super().advise_human_kp(
            campaign_id=campaign_id,
            brief=brief,
        )
        self.repo.initialize_scenario_run_state(self.run_id)
        return output


class RaisingHelpDirector:
    def __init__(self, error: Exception):
        self.error = error
        self.calls = 0

    async def advise_human_kp(
        self,
        *,
        campaign_id: str,
        brief: dict[str, Any],
    ) -> DirectorHelpOutput:
        assert campaign_id
        assert brief
        self.calls += 1
        raise self.error


class BlockingHelpDirector(FakeHelpDirector):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def advise_human_kp(
        self,
        *,
        campaign_id: str,
        brief: dict[str, Any],
    ) -> DirectorHelpOutput:
        self.started.set()
        await self.release.wait()
        return await super().advise_human_kp(
            campaign_id=campaign_id,
            brief=brief,
        )


class BlockingEntryHelpGate(DirectorHelpCallGate):
    def __init__(self) -> None:
        super().__init__(max_concurrency=1)
        self.entry_started = asyncio.Event()
        self.release = asyncio.Event()

    @asynccontextmanager
    async def reserve(self, run_id: str) -> AsyncIterator[None]:
        self.entry_started.set()
        await self.release.wait()
        async with super().reserve(run_id):
            yield


def _arrange_bound_run(
    repo: Repository,
    *,
    control_mode: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    campaign = repo.create_campaign("Need Help SQLite integration")
    session = SessionService(repo).create(campaign["id"])
    kp = repo.authenticate_access_token(session["access_token"])
    assert kp is not None
    module = repo.create_module(
        campaign["id"],
        "Open investigation",
        [
            ModuleChunk(
                title="Archive",
                text=(
                    "The investigator may search the archive with Library Use. "
                    "The records identify the subject on success."
                ),
                visibility="kp",
                spoiler_tag=None,
                scene_key="briefing",
                order_index=0,
            )
        ],
    )
    payload = json.loads(_CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    contracts = ScenarioContractService(repo)
    _, draft = contracts.compile_draft(
        module["id"],
        bind_payload_to_module(repo, module["id"], payload),
        created_by_member_id=kp.member_id,
    )
    assert draft is not None
    published = contracts.publish(
        draft["id"],
        expected_row_version=1,
        published_by_member_id=kp.member_id,
    )
    run = repo.start_campaign_module_run(
        campaign_id=campaign["id"],
        module_id=module["id"],
        current_scene_key=None,
        active_spoiler_tags=[],
        state={},
        started_by_member_id=kp.member_id,
    )
    if control_mode != run["director_control_mode"]:
        run = repo.set_module_run_control(
            run["id"],
            expected_version=run["version"],
            mode=control_mode,
            reason="Need Help integration test",
            member_id=kp.member_id,
        )
    contracts.bind_run(run["id"], published["id"])
    return campaign, run, session


def _gameplay_rows(repo: Repository) -> dict[str, tuple[tuple[Any, ...], ...]]:
    return {
        table: tuple(
            tuple(row)
            for row in repo.connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
        )
        for table in _GAMEPLAY_TABLES
    }


def test_human_kp_help_is_read_only_with_real_sqlite_repository(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "director-help.sqlite3") as connection:
        repo = Repository(connection)
        campaign, run, _ = _arrange_bound_run(repo, control_mode="human_kp")
        director = FakeHelpDirector()

        with pytest.raises(KeyError, match="Scenario run state not found"):
            repo.get_scenario_run_state(run["id"])
        assert repo.list_fact_entries(campaign["id"]) == []
        before = _gameplay_rows(repo)

        result = asyncio.run(
            DirectorHelpService(repo).advise(
                run["id"],
                "How should I resolve Search archive?",
                director,
            )
        )

        assert director.calls == 1
        assert result["status"] == "answered"
        assert result["action"]["candidate_id"] == "search-archive"
        assert result["action"]["selected_skill_key"] == "library_use"
        assert result["writes_performed"] is False
        assert result["can_execute"] is False
        assert result["state_version"] == 0
        assert _gameplay_rows(repo) == before
        assert repo.list_fact_entries(campaign["id"]) == []
        with pytest.raises(KeyError, match="Scenario run state not found"):
            repo.get_scenario_run_state(run["id"])


def test_safety_pause_blocks_help_before_fake_director_is_called(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "director-help-paused.sqlite3") as connection:
        repo = Repository(connection)
        _, run, _ = _arrange_bound_run(repo, control_mode="safety_paused")
        director = FakeHelpDirector()
        before = _gameplay_rows(repo)

        with pytest.raises(ConflictError, match="safety_paused"):
            asyncio.run(
                DirectorHelpService(repo).advise(
                    run["id"],
                    "How should I resolve Search archive?",
                    director,
                )
            )

        assert director.calls == 0
        assert _gameplay_rows(repo) == before


def test_help_rejects_advice_when_scenario_state_appears_during_model_call(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "director-help-state-fence.sqlite3") as connection:
        repo = Repository(connection)
        _, run, _ = _arrange_bound_run(repo, control_mode="human_kp")
        director = StateChangingHelpDirector(repo, run["id"])

        with pytest.raises(ConflictError, match="changed while Need Help"):
            asyncio.run(
                DirectorHelpService(repo).advise(
                    run["id"],
                    "How should I resolve Search archive?",
                    director,
                )
            )

        assert director.calls == 1
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 0
        assert repo.list_scenario_command_batches(run["id"]) == []


def test_help_http_endpoint_is_kp_only_and_does_not_call_model_for_player(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        db_path=tmp_path / "director-help-api.sqlite3",
        module_asset_root=tmp_path / "module-assets",
        local_admin_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with db_session(settings.db_path) as connection:
            repo = Repository(connection)
            campaign, run, kp_session = _arrange_bound_run(
                repo,
                control_mode="human_kp",
            )
            kp_identity = repo.authenticate_access_token(kp_session["access_token"])
            assert kp_identity is not None
            player_session = SessionService(repo).join(
                kp_session["join_code"],
                display_name="Investigator",
            )

        director = FakeHelpDirector()
        monkeypatch.setattr(
            module_runs_router,
            "create_kp_orchestrator",
            lambda *_args, **_kwargs: director,
        )
        endpoint = f"/module-runs/{run['id']}/director/help"
        question = {"question": "How should I resolve Search archive?"}

        forbidden = client.post(
            endpoint,
            headers={"Authorization": f"Bearer {player_session['access_token']}"},
            json=question,
        )

        assert forbidden.status_code == 403
        assert director.calls == 0

        response = client.post(
            endpoint,
            headers={"Authorization": f"Bearer {kp_session['access_token']}"},
            json=question,
        )

        assert response.status_code == 200
        assert response.json()["writes_performed"] is False
        assert response.json()["can_execute"] is False
        assert director.calls == 1

        history_endpoint = f"/campaigns/{campaign['id']}/director-help/audits"
        forbidden_history = client.get(
            history_endpoint,
            headers={"Authorization": f"Bearer {player_session['access_token']}"},
        )
        assert forbidden_history.status_code == 403

        history = client.get(
            history_endpoint,
            headers={"Authorization": f"Bearer {kp_session['access_token']}"},
        )
        assert history.status_code == 200
        assert history.json()["next_before_id"] is None
        assert len(history.json()["items"]) == 1
        item = history.json()["items"][0]
        advice = response.json()
        assert item["run_id"] == run["id"]
        assert item["requested_by_member_id"] == kp_identity.member_id
        assert item["question"] == question["question"]
        assert item["outcome"] == "completed"
        assert item["error_code"] is None
        assert item["advice"] == advice
        assert item["response_hash"] == hashlib.sha256(canonical_json_bytes(advice)).hexdigest()


@pytest.mark.parametrize(
    (
        "failure",
        "expected_status",
        "expected_body",
        "expected_outcome",
        "expected_audit_code",
    ),
    [
        pytest.param(
            RuntimeError("Local model provider http://127.0.0.1:8001/private is unavailable"),
            502,
            {
                "detail": "The configured model service is temporarily unavailable",
                "code": "upstream_service_error",
            },
            "failed",
            "upstream_service_error",
            id="provider-unavailable",
        ),
        pytest.param(
            CampaignAiCallCancelledError(
                "Campaign AI control changed while human KP help was running"
            ),
            409,
            {
                "detail": ("Campaign AI control changed while human KP help was running"),
                "code": "conflict",
            },
            "cancelled_control",
            "campaign_ai_call_cancelled",
            id="campaign-ai-call-cancelled",
        ),
        pytest.param(
            StructuredOutputError("model schema failed around PRIVATE_MODEL_FRAGMENT"),
            502,
            {
                "detail": "The model service returned an invalid response",
                "code": "upstream_invalid_response",
            },
            "failed",
            "upstream_invalid_response",
            id="invalid-model-output",
        ),
    ],
)
def test_help_http_failures_preserve_status_semantics_and_gameplay_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
    expected_body: dict[str, str],
    expected_outcome: str,
    expected_audit_code: str,
) -> None:
    settings = Settings(
        db_path=tmp_path / "director-help-api-failure.sqlite3",
        module_asset_root=tmp_path / "module-assets",
        local_admin_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with db_session(settings.db_path) as connection:
            repo = Repository(connection)
            campaign, run, kp_session = _arrange_bound_run(
                repo,
                control_mode="human_kp",
            )
            before = _gameplay_rows(repo)

        director = RaisingHelpDirector(failure)
        monkeypatch.setattr(
            module_runs_router,
            "create_kp_orchestrator",
            lambda *_args, **_kwargs: director,
        )

        response = client.post(
            f"/module-runs/{run['id']}/director/help",
            headers={"Authorization": f"Bearer {kp_session['access_token']}"},
            json={"question": "How should I resolve Search archive?"},
        )

        assert response.status_code == expected_status
        assert response.json() == expected_body
        assert "PRIVATE_MODEL_FRAGMENT" not in response.text
        assert director.calls == 1
        with db_session(settings.db_path) as connection:
            repo = Repository(connection)
            assert _gameplay_rows(repo) == before
            history = repo.list_director_help_audits(campaign["id"], limit=10)
            assert len(history) == 1
            assert history[0]["outcome"] == expected_outcome
            assert history[0]["error_code"] == expected_audit_code
            assert history[0]["advice"] is None
            assert history[0]["response_hash"] is None
            stored = connection.execute(
                """
                SELECT question, advice_json, error_code
                FROM director_help_audit_events
                ORDER BY sequence
                """
            ).fetchall()
            assert "127.0.0.1:8001" not in json.dumps([tuple(row) for row in stored])
            assert "PRIVATE_MODEL_FRAGMENT" not in json.dumps([tuple(row) for row in stored])


def test_help_rejects_concurrent_same_run_and_releases_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        db_path=tmp_path / "director-help-concurrency.sqlite3",
        module_asset_root=tmp_path / "module-assets",
        local_admin_enabled=False,
    )
    app = create_app(settings)
    with db_session(settings.db_path) as connection:
        repo = Repository(connection)
        campaign, run, kp_session = _arrange_bound_run(
            repo,
            control_mode="human_kp",
        )
        before = _gameplay_rows(repo)

    director = BlockingHelpDirector()
    monkeypatch.setattr(
        module_runs_router,
        "create_kp_orchestrator",
        lambda *_args, **_kwargs: director,
    )
    endpoint = f"/module-runs/{run['id']}/director/help"
    headers = {"Authorization": f"Bearer {kp_session['access_token']}"}
    question = {"question": "How should I resolve Search archive?"}

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            first_task = asyncio.create_task(client.post(endpoint, headers=headers, json=question))
            await asyncio.wait_for(director.started.wait(), timeout=1)
            duplicate = await asyncio.wait_for(
                client.post(endpoint, headers=headers, json=question),
                timeout=1,
            )
            director.release.set()
            first = await asyncio.wait_for(first_task, timeout=2)
        return first, duplicate

    first, duplicate = asyncio.run(exercise())

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "director_help_in_progress"
    assert director.calls == 1
    assert app.state.director_help_call_gate.active_count() == 0
    with db_session(settings.db_path) as connection:
        repo = Repository(connection)
        assert _gameplay_rows(repo) == before
        history = repo.list_director_help_audits(campaign["id"], limit=10)
        assert [item["outcome"] for item in history] == [
            "rejected_busy",
            "completed",
        ]
        assert history[0]["error_code"] == "director_help_in_progress"
        assert history[0]["advice"] is None
        assert history[1]["error_code"] is None
        assert history[1]["advice"] == first.json()


def test_help_does_not_return_unrecorded_advice_when_completion_audit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        db_path=tmp_path / "director-help-audit-failure.sqlite3",
        module_asset_root=tmp_path / "module-assets",
        local_admin_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with db_session(settings.db_path) as connection:
            repo = Repository(connection)
            campaign, run, kp_session = _arrange_bound_run(
                repo,
                control_mode="human_kp",
            )
            before = _gameplay_rows(repo)

        director = FakeHelpDirector()
        monkeypatch.setattr(
            module_runs_router,
            "create_kp_orchestrator",
            lambda *_args, **_kwargs: director,
        )

        def fail_completion_audit(*_args: object, **_kwargs: object) -> str:
            raise DirectorHelpAuditUnavailableError(
                "Need Help is unavailable because its audit record could not be stored"
            )

        monkeypatch.setattr(
            module_runs_router,
            "complete_director_help_audit",
            fail_completion_audit,
        )
        response = client.post(
            f"/module-runs/{run['id']}/director/help",
            headers={"Authorization": f"Bearer {kp_session['access_token']}"},
            json={"question": "How should I resolve Search archive?"},
        )

        assert response.status_code == 503
        assert response.json() == {
            "detail": ("Need Help is unavailable because its audit record could not be stored"),
            "code": "director_help_audit_unavailable",
        }
        assert director.calls == 1
        assert app.state.director_help_call_gate.active_count() == 0
        with db_session(settings.db_path) as connection:
            repo = Repository(connection)
            assert _gameplay_rows(repo) == before
            history = repo.list_director_help_audits(campaign["id"], limit=10)
            assert len(history) == 1
            assert history[0]["outcome"] == "requested"
            assert history[0]["advice"] is None
            assert (
                connection.execute("SELECT COUNT(*) FROM director_help_audit_events").fetchone()[0]
                == 1
            )


def test_blank_help_question_never_calls_model_or_creates_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        db_path=tmp_path / "director-help-blank.sqlite3",
        module_asset_root=tmp_path / "module-assets",
        local_admin_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with db_session(settings.db_path) as connection:
            repo = Repository(connection)
            _, run, kp_session = _arrange_bound_run(
                repo,
                control_mode="human_kp",
            )

        director = FakeHelpDirector()
        monkeypatch.setattr(
            module_runs_router,
            "create_kp_orchestrator",
            lambda *_args, **_kwargs: director,
        )
        response = client.post(
            f"/module-runs/{run['id']}/director/help",
            headers={"Authorization": f"Bearer {kp_session['access_token']}"},
            json={"question": " \n\t "},
        )

        assert response.status_code == 422
        assert director.calls == 0
        with db_session(settings.db_path) as connection:
            assert (
                connection.execute("SELECT COUNT(*) FROM director_help_audit_events").fetchone()[0]
                == 0
            )


def test_cancellation_before_gate_entry_records_terminal_without_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        db_path=tmp_path / "director-help-entry-cancel.sqlite3",
        module_asset_root=tmp_path / "module-assets",
        local_admin_enabled=False,
    )
    app = create_app(settings)
    with db_session(settings.db_path) as connection:
        repo = Repository(connection)
        campaign, run, kp_session = _arrange_bound_run(
            repo,
            control_mode="human_kp",
        )

    director = FakeHelpDirector()
    gate = BlockingEntryHelpGate()
    app.state.director_help_call_gate = gate
    monkeypatch.setattr(
        module_runs_router,
        "create_kp_orchestrator",
        lambda *_args, **_kwargs: director,
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            request_task = asyncio.create_task(
                client.post(
                    f"/module-runs/{run['id']}/director/help",
                    headers={"Authorization": f"Bearer {kp_session['access_token']}"},
                    json={"question": "How should I resolve Search archive?"},
                )
            )
            await asyncio.wait_for(gate.entry_started.wait(), timeout=1)
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task

    asyncio.run(exercise())

    assert director.calls == 0
    assert gate.active_count() == 0
    with db_session(settings.db_path) as connection:
        repo = Repository(connection)
        history = repo.list_director_help_audits(campaign["id"], limit=10)
        assert len(history) == 1
        assert history[0]["outcome"] == "failed"
        assert history[0]["error_code"] == "request_cancelled"
        assert history[0]["advice"] is None


@pytest.mark.parametrize("block_after_commit", [False, True])
def test_cancellation_during_completed_audit_keeps_one_completed_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    block_after_commit: bool,
) -> None:
    settings = Settings(
        db_path=tmp_path / f"director-help-complete-cancel-{block_after_commit}.sqlite3",
        module_asset_root=tmp_path / "module-assets",
        local_admin_enabled=False,
    )
    app = create_app(settings)
    with db_session(settings.db_path) as connection:
        repo = Repository(connection)
        campaign, run, kp_session = _arrange_bound_run(
            repo,
            control_mode="human_kp",
        )

    director = FakeHelpDirector()
    monkeypatch.setattr(
        module_runs_router,
        "create_kp_orchestrator",
        lambda *_args, **_kwargs: director,
    )
    original_complete = module_runs_router.complete_director_help_audit
    blocked = threading.Event()
    release = threading.Event()

    def controlled_complete(*args: Any, **kwargs: Any) -> str:
        if not block_after_commit:
            blocked.set()
            release.wait(timeout=1)
        result = original_complete(*args, **kwargs)
        if block_after_commit:
            blocked.set()
            release.wait(timeout=1)
        return result

    monkeypatch.setattr(
        module_runs_router,
        "complete_director_help_audit",
        controlled_complete,
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            request_task = asyncio.create_task(
                client.post(
                    f"/module-runs/{run['id']}/director/help",
                    headers={"Authorization": f"Bearer {kp_session['access_token']}"},
                    json={"question": "How should I resolve Search archive?"},
                )
            )
            assert await asyncio.to_thread(blocked.wait, 1)
            assert app.state.director_help_call_gate.active_count() == 1
            request_task.cancel()
            await asyncio.sleep(0.02)
            assert not request_task.done()
            assert app.state.director_help_call_gate.active_count() == 1
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await request_task

    asyncio.run(exercise())

    assert director.calls == 1
    assert app.state.director_help_call_gate.active_count() == 0
    with db_session(settings.db_path) as connection:
        repo = Repository(connection)
        history = repo.list_director_help_audits(campaign["id"], limit=10)
        assert len(history) == 1
        assert history[0]["outcome"] == "completed"
        assert history[0]["error_code"] is None
        assert history[0]["advice"] is not None
        assert (
            connection.execute("SELECT COUNT(*) FROM director_help_audit_events").fetchone()[0] == 2
        )


def test_help_disconnect_cancels_operation() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class DisconnectRequest:
        async def is_disconnected(self) -> bool:
            await started.wait()
            return True

    async def operation() -> dict[str, Any]:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    async def exercise() -> None:
        with pytest.raises(DirectorHelpClientDisconnectedError):
            await module_runs_router._complete_help_while_connected(
                cast(Request, DisconnectRequest()),
                operation(),
            )

    asyncio.run(exercise())

    assert cancelled.is_set()
