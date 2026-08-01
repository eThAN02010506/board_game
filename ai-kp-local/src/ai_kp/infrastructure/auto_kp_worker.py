"""Durable local worker for queued Auto KP jobs."""

from __future__ import annotations

import asyncio
import sqlite3
from threading import Event, RLock, Thread
from typing import Any

from ai_kp.application.action_adjudication_service import ActionAdjudicationService
from ai_kp.application.auto_turn_service import AutoTurnService
from ai_kp.application.auto_world_expansion_service import AutoWorldExpansionService
from ai_kp.application.module_run_service import ModuleRunService
from ai_kp.application.parallel_action_settlement_service import (
    ParallelActionSettlementCommand,
    ParallelActionSettlementService,
)
from ai_kp.application.turn_service import TurnService, WorldExpansionCommand
from ai_kp.bootstrap.settings import Settings
from ai_kp.director.orchestrator import KpOrchestrator
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.infrastructure.llm.call_registry import CampaignAiCallRegistry
from ai_kp.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from ai_kp.platform.sessions.models import AuthenticatedMember


class AutoKpWorker:
    """One local thread that drains the SQLite-backed Auto KP queue."""

    def __init__(
        self,
        settings: Settings,
        *,
        poll_interval_seconds: float = 0.5,
        call_registry: CampaignAiCallRegistry | None = None,
    ):
        self.settings = settings
        self.poll_interval_seconds = poll_interval_seconds
        self.call_registry = call_registry
        self._stop_event = Event()
        self._wake_event = Event()
        self._ready_event = Event()
        self._thread: Thread | None = None
        self._startup_error: BaseException | None = None
        self._last_error: str | None = None
        self._lock = RLock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._ready_event.clear()
            self._startup_error = None
            self._last_error = None
            self._thread = Thread(
                target=self._run,
                name="ai-kp-auto-kp-worker",
                daemon=True,
            )
            thread = self._thread
            thread.start()
        if not self._ready_event.wait(timeout=30):
            self.stop()
            raise RuntimeError("Auto KP worker did not become ready")
        if self._startup_error is not None:
            error = self._startup_error
            self.stop()
            raise RuntimeError("Auto KP worker failed to start") from error

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
            self._wake_event.set()
        thread.join()
        with self._lock:
            if self._thread is thread:
                self._thread = None

    def wake(self) -> None:
        self._wake_event.set()

    def _run(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = connect(
                self.settings.db_path,
                synchronous=self.settings.sqlite_synchronous,
            )
            init_db(connection)
            repo = Repository(connection)
            repo.recover_stale_auto_kp_jobs()
            connection.commit()
            self._ready_event.set()

            while not self._stop_event.is_set():
                try:
                    job = repo.claim_next_auto_kp_job(worker_id="auto-kp-worker")
                    connection.commit()
                except sqlite3.Error as exc:
                    connection.rollback()
                    self._last_error = str(exc)
                    self._wait_for_work()
                    continue
                if job is None:
                    self._wait_for_work()
                    continue
                process_claimed_auto_kp_job(
                    connection,
                    job,
                    settings=self.settings,
                    call_registry=self.call_registry,
                )
        except BaseException as exc:  # noqa: BLE001 - propagate startup failure to lifespan
            self._startup_error = exc
            self._last_error = str(exc)
            self._ready_event.set()
        finally:
            if connection is not None:
                connection.close()

    def _wait_for_work(self) -> None:
        self._wake_event.wait(timeout=self.poll_interval_seconds)
        self._wake_event.clear()


def process_claimed_auto_kp_job(
    connection: sqlite3.Connection,
    job: dict,
    *,
    settings: Settings,
    call_registry: CampaignAiCallRegistry | None = None,
) -> None:
    repo = Repository(connection)
    job_id = str(job["id"])
    expected_attempt = int(job["attempt_count"])
    try:
        result = asyncio.run(
            _dispatch_auto_kp_job(
                repo,
                job,
                settings=settings,
                call_registry=call_registry,
            )
        )
        if result["status"] == "failed":
            raise RuntimeError(str(result.get("message") or "Auto KP job failed"))
        status = "needs_attention" if result["status"] == "needs_attention" else "succeeded"
        repo.complete_auto_kp_job(
            job_id,
            expected_attempt=expected_attempt,
            status=status,
            stage=str(result.get("stage") or result["status"]),
            result=result,
        )
        connection.commit()
    except Exception as exc:  # noqa: BLE001 - durable job boundary records failures
        connection.rollback()
        try:
            Repository(connection).fail_auto_kp_job(
                job_id,
                expected_attempt=expected_attempt,
                error=str(exc),
            )
            connection.commit()
        except (KeyError, sqlite3.Error, ValueError):
            connection.rollback()


async def _dispatch_auto_kp_job(
    repo: Repository,
    job: dict,
    *,
    settings: Settings,
    call_registry: CampaignAiCallRegistry | None,
) -> dict[str, Any]:
    director = _director(repo, settings, call_registry)
    job_type = str(job["job_type"])
    payload = dict(job.get("payload") or {})
    if job_type == "player_action":
        action = repo.get_player_action(str(job["resource_id"]))
        run = repo.get_active_campaign_module_run(str(action["campaign_id"]))
        if run is not None:
            analysis = ModuleRunService(repo).analyze(
                str(run["id"]), str(action["action_text"])
            )
            if analysis["decision"] == "world_gap":
                return await _adjudicate_player_world_gap(
                    repo,
                    action,
                    run_id=str(run["id"]),
                    director=director,
                    source_model=settings.llm_model,
                )
        result = await AutoTurnService(repo).advance_player_action(
            str(job["resource_id"]),
            director=director,
            source_model=settings.llm_model,
        )
        return {"status": result.status, "stage": "player_action", **result.as_dict()}
    if job_type == "check_consequence":
        result = await AutoTurnService(repo).advance_after_check(
            str(job["resource_id"]),
            director=director,
            source_model=settings.llm_model,
        )
        if result is None:
            return {
                "status": "needs_attention",
                "stage": "check_consequence",
                "message": "检定尚不能自动生成后果。",
            }
        return {"status": result.status, "stage": "check_consequence", **result.as_dict()}
    if job_type == "parallel_actions":
        action_ids = tuple(str(item) for item in payload.get("action_ids") or ())
        identity = _kp_identity_for_parallel_actions(repo, action_ids)
        result = await ParallelActionSettlementService(repo).settle(
            str(job["campaign_id"]),
            identity,
            ParallelActionSettlementCommand(
                action_ids=action_ids,
                auto_approve=False,
                player_confirmation=True,
            ),
            director,
            source_model=settings.llm_model,
        )
        return {"status": result.status, "stage": "parallel_actions", **result.as_dict()}
    if job_type == "world_expansion":
        run_id = str(payload.get("run_id") or job.get("run_id") or "")
        player_intent = str(payload.get("player_intent") or "")
        if not run_id or not player_intent.strip():
            raise ValueError("world_expansion jobs require run_id and player_intent")
        identity = _kp_identity_for_campaign(repo, str(job["campaign_id"]))
        result = await AutoWorldExpansionService(repo).create_and_maybe_materialize(
            WorldExpansionCommand(
                run_id=run_id,
                player_intent=player_intent,
                pc_id=payload.get("pc_id"),
                map_id=payload.get("map_id"),
            ),
            identity,
            director,
            source_model=settings.llm_model,
        )
        return {"status": result.status, "stage": "world_expansion", **result.as_dict()}
    raise ValueError(f"Unsupported Auto KP job type: {job_type}")


async def _adjudicate_player_world_gap(
    repo: Repository,
    action: dict,
    *,
    run_id: str,
    director: KpOrchestrator,
    source_model: str,
) -> dict[str, Any]:
    """Create a candidate without materializing it before player confirmation."""
    proposal = await TurnService(repo).create_world_expansion_proposal(
        WorldExpansionCommand(
            run_id=run_id,
            player_intent=str(action["action_text"]),
            pc_id=action.get("pc_id"),
            map_id=action.get("map_id"),
        ),
        _kp_identity_for_session(
            repo, str(action["campaign_id"]), str(action["session_id"])
        ),
        director,
        source_model=source_model,
    )
    policy = AutoWorldExpansionService(repo).policy_decision(proposal)
    candidate = proposal["world_expansion"]["candidate"]
    repo.add_proposal_action(
        str(proposal["id"]),
        "action_ruling",
        actor="system",
        note="player-confirmed world expansion boundary",
        payload={
            "goal": str(action["action_text"])[:500],
            "method": "受约束世界补全",
            "target": str(candidate.get("subject") or "当前场景")[:300],
            "feasibility": "possible" if policy["allowed"] else "impossible",
            "resolution": "automatic" if policy["allowed"] else "no_roll",
            "reason": (
                "候选通过低副作用世界补全策略，仍需玩家确认。"
                if policy["allowed"]
                else "世界补全策略阻止自动落地："
                + ", ".join(policy["blockers"])
            ),
            "maximum_effect": str(candidate.get("proposal") or "不写入状态")[:1000],
            "alternative": "补充目标或改为当前已存在场景内的行动。",
        },
    )
    proposal = repo.get_turn_proposal(str(proposal["id"]))
    repo.link_player_action_to_proposal(
        str(action["id"]), str(proposal["id"]), str(action["campaign_id"])
    )
    adjudication = ActionAdjudicationService(repo).create(
        action,
        proposal,
        source_model=source_model,
        source_error=None if policy["allowed"] else "世界补全超出自动落地策略",
    )
    return {
        "status": "awaiting_confirmation",
        "stage": "world_expansion",
        "player_action": repo.get_player_action(str(action["id"])),
        "proposal": proposal,
        "checks": [],
        "adjudication": adjudication,
        "policy": policy,
        "message": "世界补全候选已生成；确认前不会写入世界状态。",
    }


def _director(
    repo: Repository,
    settings: Settings,
    call_registry: CampaignAiCallRegistry | None,
) -> KpOrchestrator:
    return KpOrchestrator(
        repo.connection,
        OpenAICompatibleClient(
            settings.llm_base_url,
            settings.llm_api_key,
            settings.llm_model,
        ),
        call_registry=call_registry,
    )


def _kp_identity_for_parallel_actions(
    repo: Repository,
    action_ids: tuple[str, ...],
) -> AuthenticatedMember:
    if not action_ids:
        raise ValueError("parallel_actions jobs require action_ids")
    action = repo.get_player_action(action_ids[0])
    return _kp_identity_for_session(
        repo,
        str(action["campaign_id"]),
        str(action["session_id"]),
    )


def _kp_identity_for_campaign(repo: Repository, campaign_id: str) -> AuthenticatedMember:
    row = repo.connection.execute(
        """
        SELECT id, session_id, campaign_id, display_name
        FROM session_members
        WHERE campaign_id = ? AND role = 'kp' AND revoked_at IS NULL
        ORDER BY joined_at, id
        LIMIT 1
        """,
        (campaign_id,),
    ).fetchone()
    if row is None:
        raise ValueError("No active KP member exists for Auto KP job")
    return _identity_from_member_row(row)


def _kp_identity_for_session(
    repo: Repository,
    campaign_id: str,
    session_id: str,
) -> AuthenticatedMember:
    row = repo.connection.execute(
        """
        SELECT id, session_id, campaign_id, display_name
        FROM session_members
        WHERE campaign_id = ? AND session_id = ? AND role = 'kp'
          AND revoked_at IS NULL
        ORDER BY joined_at, id
        LIMIT 1
        """,
        (campaign_id, session_id),
    ).fetchone()
    if row is None:
        raise ValueError("No active KP member exists for Auto KP job session")
    return _identity_from_member_row(row)


def _identity_from_member_row(row: Any) -> AuthenticatedMember:
    return AuthenticatedMember(
        member_id=str(row["id"]),
        session_id=str(row["session_id"]),
        campaign_id=str(row["campaign_id"]),
        role="kp",
        display_name=f"{row['display_name']} · Auto KP Worker",
        pc_id=None,
    )


__all__ = ["AutoKpWorker", "process_claimed_auto_kp_job"]
