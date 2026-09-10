"""Authorized selection, progression, and safe projections for active modules."""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from contextlib import suppress
from time import perf_counter
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from ai_kp.api.authz import require_approved_pc_binding, require_campaign_role
from ai_kp.api.auto_kp import player_auto_kp_job
from ai_kp.api.dependencies import get_app_settings, get_identity, get_repo
from ai_kp.api.director_help_audits import (
    DirectorHelpAuditIoResult,
    begin_director_help_audit,
    complete_director_help_audit,
    settle_director_help_audit_io,
    settle_director_help_audit_task,
    terminate_director_help_audit,
)
from ai_kp.api.llm import create_kp_orchestrator
from ai_kp.api.schemas import (
    AutoKpJobCreate,
    AutomationLevelUpdate,
    DirectorAnalysisRequest,
    DirectorControlUpdate,
    DirectorHelpAuditPage,
    DirectorHelpRequest,
    DirectorHelpResponse,
    ModuleRunEntityStateUpdate,
    ModuleRunStart,
    ModuleRunUpdate,
    ModuleSceneTransition,
    WorldExpansionProposalRequest,
)
from ai_kp.application.auto_kp_job_authority import AutoKpJobAuthorityValidator
from ai_kp.application.auto_world_expansion_service import AutoWorldExpansionService
from ai_kp.application.consequence_signal_service import ConsequenceSignalService
from ai_kp.application.director_help_audit_service import (
    DirectorHelpAuditAttempt,
    DirectorHelpAuditService,
    DirectorHelpTerminalOutcome,
)
from ai_kp.application.director_help_service import DirectorHelpService
from ai_kp.application.errors import (
    ConflictError,
    DirectorHelpAuditUnavailableError,
    DirectorHelpClientDisconnectedError,
    InvalidInputError,
    KpSessionEndedError,
    UpstreamInvalidResponseError,
    UpstreamServiceError,
)
from ai_kp.application.module_run_service import (
    AutomationLevelCommand,
    DirectorControlCommand,
    EntityStateCommand,
    ModuleRunService,
    SceneTransitionCommand,
    StartModuleRunCommand,
)
from ai_kp.application.scenario_overlay_service import ScenarioOverlayService
from ai_kp.application.turn_service import TurnService, WorldExpansionCommand
from ai_kp.bootstrap.settings import Settings
from ai_kp.director.errors import CampaignAiCallCancelledError
from ai_kp.director.turn_output import StructuredOutputError
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.llm.director_help_call_gate import (
    DirectorHelpCallGate,
    DirectorHelpCapacityExceededError,
    DirectorHelpRunInProgressError,
)
from ai_kp.platform.modules.public_opening import extract_public_opening
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter(tags=["module runs"])
logger = logging.getLogger(__name__)

_CLIENT_DISCONNECT_POLL_SECONDS = 0.1


def _elapsed_milliseconds(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


class _DirectorHelpTerminalLatch:
    """Freeze exactly one terminal proposal and drain its worker before release."""

    def __init__(
        self,
        settings: Settings,
        attempt: DirectorHelpAuditAttempt,
        started_at: float,
    ) -> None:
        self.settings = settings
        self.attempt = attempt
        self.started_at = started_at
        self._proposal: tuple[str, ...] | None = None
        self._worker: asyncio.Task[Any] | None = None

    @property
    def started(self) -> bool:
        return self._worker is not None

    async def complete(
        self,
        advice: dict[str, Any],
    ) -> DirectorHelpAuditIoResult[str]:
        return cast(
            DirectorHelpAuditIoResult[str],
            await self._finish(
                ("completed",),
                complete_director_help_audit,
                self.settings,
                self.attempt,
                advice,
                duration_ms=_elapsed_milliseconds(self.started_at),
            ),
        )

    async def terminate(
        self,
        *,
        outcome: DirectorHelpTerminalOutcome,
        error_code: str,
    ) -> DirectorHelpAuditIoResult[None]:
        return cast(
            DirectorHelpAuditIoResult[None],
            await self._finish(
                (outcome, error_code),
                terminate_director_help_audit,
                self.settings,
                self.attempt,
                outcome=outcome,
                error_code=error_code,
                duration_ms=_elapsed_milliseconds(self.started_at),
            ),
        )

    async def drain(self) -> DirectorHelpAuditIoResult[Any]:
        if self._worker is None:
            raise RuntimeError("Need Help terminal worker has not started")
        return await settle_director_help_audit_task(self._worker)

    async def _finish(
        self,
        proposal: tuple[str, ...],
        operation: Callable[..., Any],
        /,
        *args: object,
        **kwargs: object,
    ) -> DirectorHelpAuditIoResult[Any]:
        if self._worker is None:
            self._proposal = proposal
            self._worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        elif self._proposal != proposal:
            raise RuntimeError("Need Help terminal outcome is already frozen")
        return await settle_director_help_audit_task(self._worker)


def _raise_after_audit(result: DirectorHelpAuditIoResult[Any]) -> None:
    if result.cancellation is not None:
        if result.error is not None:
            logger.error(
                "Need Help audit I/O failed while request cancellation was pending (%s)",
                type(result.error).__name__,
            )
        raise result.cancellation
    if result.error is not None:
        raise result.error


def _log_cancelled_audit_error(
    result: DirectorHelpAuditIoResult[Any],
    message: str,
) -> None:
    if result.error is not None:
        logger.error("%s (%s)", message, type(result.error).__name__)


async def _begin_help_attempt(
    settings: Settings,
    *,
    campaign_id: str,
    run_id: str,
    requested_by_member_id: str,
    request_id: str,
    question: str,
    started_at: float,
) -> DirectorHelpAuditAttempt:
    """Persist requested, or close it if cancellation races the write."""

    result = await settle_director_help_audit_io(
        begin_director_help_audit,
        settings,
        campaign_id=campaign_id,
        run_id=run_id,
        requested_by_member_id=requested_by_member_id,
        request_id=request_id,
        question=question,
    )
    if result.error is not None:
        if result.cancellation is not None:
            _log_cancelled_audit_error(
                result,
                "Cancelled Need Help request could not create its audit",
            )
            raise result.cancellation
        raise result.error
    attempt = cast(DirectorHelpAuditAttempt, result.value)
    if result.cancellation is not None:
        terminal = _DirectorHelpTerminalLatch(settings, attempt, started_at)
        terminal_result = await terminal.terminate(
            outcome="failed",
            error_code="request_cancelled",
        )
        _log_cancelled_audit_error(
            terminal_result,
            "Cancelled Need Help request audit could not be closed",
        )
        raise result.cancellation
    return attempt


def _require_run_kp(
    run_id: str,
    identity: AuthenticatedMember,
    repo: Repository,
) -> dict:
    run = repo.get_campaign_module_run(run_id)
    require_campaign_role(identity, run["campaign_id"], ("kp",))
    return run


async def _complete_help_while_connected(
    request: Request,
    operation: Coroutine[Any, Any, dict[str, Any]],
) -> dict[str, Any]:
    """Cancel model work when the requesting KP has actually disconnected."""

    help_task = asyncio.create_task(operation)
    try:
        while True:
            completed, _pending = await asyncio.wait(
                (help_task,),
                timeout=_CLIENT_DISCONNECT_POLL_SECONDS,
            )
            if help_task in completed:
                return await help_task
            if await request.is_disconnected():
                help_task.cancel()
                with suppress(asyncio.CancelledError, CampaignAiCallCancelledError):
                    await help_task
                raise DirectorHelpClientDisconnectedError(
                    "Need Help stopped because the requesting KP disconnected"
                )
    finally:
        if not help_task.done():
            help_task.cancel()
        await asyncio.gather(help_task, return_exceptions=True)


async def _run_reserved_director_help(
    *,
    run_id: str,
    question: str,
    request: Request,
    repo: Repository,
    settings: Settings,
    terminal: _DirectorHelpTerminalLatch,
) -> dict[str, Any]:
    """Finish exactly one terminal audit before releasing the run's gate slot."""

    try:
        raw_advice = await _complete_help_while_connected(
            request,
            DirectorHelpService(repo).advise(
                run_id,
                question,
                create_kp_orchestrator(repo, settings, request),
            ),
        )
        advice = DirectorHelpResponse.model_validate(raw_advice).model_dump(mode="json")
        _raise_after_audit(await terminal.complete(advice))
        return advice
    except DirectorHelpAuditUnavailableError:
        raise
    except DirectorHelpClientDisconnectedError:
        _raise_after_audit(
            await terminal.terminate(
                outcome="cancelled_client",
                error_code="client_disconnected",
            )
        )
        raise
    except KpSessionEndedError as exc:
        _raise_after_audit(
            await terminal.terminate(
                outcome="failed",
                error_code="kp_session_ended",
            )
        )
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except CampaignAiCallCancelledError:
        _raise_after_audit(
            await terminal.terminate(
                outcome="cancelled_control",
                error_code="campaign_ai_call_cancelled",
            )
        )
        raise
    except ConflictError:
        _raise_after_audit(
            await terminal.terminate(
                outcome="failed",
                error_code="conflict",
            )
        )
        raise
    except InvalidInputError:
        _raise_after_audit(
            await terminal.terminate(
                outcome="failed",
                error_code="invalid_input",
            )
        )
        raise
    except UpstreamServiceError:
        _raise_after_audit(
            await terminal.terminate(
                outcome="failed",
                error_code="upstream_service_error",
            )
        )
        raise
    except StructuredOutputError as exc:
        _raise_after_audit(
            await terminal.terminate(
                outcome="failed",
                error_code="upstream_invalid_response",
            )
        )
        raise UpstreamInvalidResponseError(
            "The model service returned an invalid response"
        ) from exc
    except RuntimeError as exc:
        _raise_after_audit(
            await terminal.terminate(
                outcome="failed",
                error_code="upstream_service_error",
            )
        )
        raise UpstreamServiceError(
            "The configured model service is temporarily unavailable"
        ) from exc
    except ValidationError as exc:
        _raise_after_audit(
            await terminal.terminate(
                outcome="failed",
                error_code="internal_error",
            )
        )
        raise HTTPException(
            status_code=500,
            detail="Need Help response failed server validation",
        ) from exc
    except Exception:
        _raise_after_audit(
            await terminal.terminate(
                outcome="failed",
                error_code="internal_error",
            )
        )
        raise


@router.get("/campaigns/{campaign_id}/consequence-signals")
def get_campaign_consequence_signals(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp", "player", "observer"))
    return ConsequenceSignalService(repo).campaign_view(
        campaign_id,
        audience="kp" if identity.role == "kp" else "table",
    )


@router.get("/campaigns/{campaign_id}/module-runs")
def list_campaign_module_runs(
    campaign_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.list_campaign_module_runs(
        campaign_id,
        limit=limit,
        offset=offset,
    )


@router.get("/campaigns/{campaign_id}/module-runs/current")
def get_active_campaign_module_run(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict | None:
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.get_active_campaign_module_run(campaign_id)


@router.get(
    "/campaigns/{campaign_id}/director-help/audits",
    response_model=DirectorHelpAuditPage,
)
def list_campaign_director_help_audits(
    campaign_id: str,
    limit: int = Query(default=10, ge=1, le=50),
    before_id: str | None = Query(default=None, min_length=1, max_length=160),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict[str, Any]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return DirectorHelpAuditService(repo).page(
        campaign_id,
        limit=limit,
        before_id=before_id,
    )


@router.get("/campaigns/{campaign_id}/module-runs/play-state")
def get_campaign_module_play_state(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    """Expose only the run lifecycle needed to enable the shared play surface."""

    require_campaign_role(identity, campaign_id, ("kp", "player", "observer"))
    active = repo.get_active_campaign_module_run(campaign_id)
    if active is not None:
        opening_narration = extract_public_opening(
            repo.list_module_chunks(
                active["module_id"],
                allowed_visibility=("player", "table", "kp", "secret"),
                spoiler_tags=None,
            )
        )
        return {
            "accepts_actions": True,
            "status": "active",
            "module_title": active["module_title"],
            "completed_at": None,
            "ending_id": None,
            "ending_title": None,
            "opening_narration": opening_narration,
        }
    recent = repo.list_campaign_module_runs(campaign_id, limit=1)
    if not recent:
        return {
            "accepts_actions": True,
            "status": "none",
            "module_title": None,
            "completed_at": None,
            "ending_id": None,
            "ending_title": None,
            "opening_narration": None,
        }
    latest = recent[0]
    ending_id = None
    ending_title = None
    if latest["status"] == "completed":
        try:
            state = repo.get_scenario_run_state(latest["id"])
            ending_id = state["snapshot"].ending_id
            if ending_id:
                binding = repo.get_module_run_contract_binding(latest["id"])
                ending = next(
                    (item for item in binding["contract"].endings if item.ending_id == ending_id),
                    None,
                )
                ending_title = ending.title if ending is not None else None
        except KeyError:
            # Legacy/manual completions may not have a kernel snapshot or binding.
            pass
    return {
        "accepts_actions": False,
        "status": latest["status"],
        "module_title": latest["module_title"],
        "completed_at": latest.get("completed_at"),
        "ending_id": ending_id,
        "ending_title": ending_title,
        "opening_narration": None,
    }


@router.get("/campaigns/{campaign_id}/auto-kp/jobs")
def list_auto_kp_jobs(
    campaign_id: str,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id)
    if identity.role == "kp":
        return repo.list_auto_kp_jobs(campaign_id, status=status, limit=limit)
    jobs = repo.list_player_auto_kp_jobs(
        campaign_id,
        identity.member_id,
        status=status,
        limit=limit,
    )
    return [player_auto_kp_job(job) for job in jobs]


@router.post("/campaigns/{campaign_id}/auto-kp/jobs")
def enqueue_auto_kp_job(
    campaign_id: str,
    payload: AutoKpJobCreate,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    job_payload = dict(payload.payload)
    # The durable session fence is server-derived.  A client may repeat it for
    # diagnostics, but it cannot redirect the worker to another session.
    job_payload.setdefault("session_id", identity.session_id)
    AutoKpJobAuthorityValidator(repo).validate(
        campaign_id=campaign_id,
        run_id=payload.run_id,
        job_type=payload.job_type,
        resource_id=payload.resource_id,
        payload=job_payload,
        expected_session_id=identity.session_id,
    )
    job = repo.enqueue_auto_kp_job(
        campaign_id=campaign_id,
        run_id=payload.run_id,
        job_type=payload.job_type,
        resource_id=payload.resource_id,
        idempotency_key=payload.idempotency_key,
        payload=job_payload,
        max_attempts=payload.max_attempts,
    )
    worker = getattr(request.app.state, "auto_kp_worker", None)
    if worker is not None:
        worker.wake()
    return job


@router.post("/auto-kp/jobs/{job_id}/retry")
def retry_auto_kp_job(
    job_id: str,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    job = repo.get_auto_kp_job(job_id)
    require_campaign_role(identity, job["campaign_id"])
    if identity.role == "player":
        job = repo.get_player_auto_kp_job(job_id, identity.member_id)
        if job["status"] == "needs_attention":
            raise HTTPException(
                status_code=403,
                detail="Policy-blocked Auto KP jobs require KP review",
            )
        if job["status"] != "failed":
            raise HTTPException(
                status_code=409,
                detail="Players can retry only failed Auto KP jobs",
            )
    retried = repo.retry_auto_kp_job(job_id)
    worker = getattr(request.app.state, "auto_kp_worker", None)
    if worker is not None:
        worker.wake()
    return retried if identity.role == "kp" else player_auto_kp_job(retried)


@router.post("/campaigns/{campaign_id}/auto-kp/jobs/recover")
def recover_auto_kp_jobs(
    campaign_id: str,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    recovered = repo.recover_stale_auto_kp_jobs(campaign_id=campaign_id)
    worker = getattr(request.app.state, "auto_kp_worker", None)
    if worker is not None:
        worker.wake()
    return {"recovered": recovered, "jobs": repo.list_auto_kp_jobs(campaign_id)}


@router.post("/campaigns/{campaign_id}/module-runs")
def start_campaign_module_run(
    campaign_id: str,
    payload: ModuleRunStart,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return ModuleRunService(repo).start(
        campaign_id,
        StartModuleRunCommand(
            module_id=payload.module_id,
            current_scene_key=payload.current_scene_key,
            active_spoiler_tags=payload.active_spoiler_tags,
            state=payload.state,
        ),
        member_id=identity.member_id,
    )


@router.patch("/module-runs/{run_id}")
def update_campaign_module_run(
    run_id: str,
    payload: ModuleRunUpdate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_run_kp(run_id, identity, repo)
    return ModuleRunService(repo).update(
        run_id,
        payload.model_dump(exclude_unset=True),
    )


@router.get("/module-runs/{run_id}/director-state")
def get_module_run_director_state(
    run_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    run = _require_run_kp(run_id, identity, repo)
    return {
        "run": run,
        "entity_states": repo.list_module_run_entity_states(run_id),
        "scene_events": repo.list_module_run_scene_events(run_id),
        "entity_state_events": repo.list_module_run_entity_state_events(run_id),
        "control_events": repo.list_module_run_control_events(run_id),
        "automation_events": repo.list_module_run_automation_events(run_id),
        "auto_kp_jobs": repo.list_auto_kp_jobs(str(run["campaign_id"]), limit=12),
    }


@router.post("/module-runs/{run_id}/director/control")
async def update_director_control(
    run_id: str,
    payload: DirectorControlUpdate,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_run_kp(run_id, identity, repo)
    updated = ModuleRunService(repo).set_control(
        run_id,
        DirectorControlCommand(
            expected_version=payload.expected_version,
            mode=payload.mode,
            reason=payload.reason,
        ),
        member_id=identity.member_id,
    )
    if payload.mode != "ai_assist":
        request.app.state.campaign_ai_calls.cancel_campaign(str(updated["campaign_id"]))
    return updated


@router.post("/module-runs/{run_id}/automation")
def update_automation_level(
    run_id: str,
    payload: AutomationLevelUpdate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_run_kp(run_id, identity, repo)
    return ModuleRunService(repo).set_automation_level(
        run_id,
        AutomationLevelCommand(
            expected_version=payload.expected_version,
            level=payload.level,
            reason=payload.reason,
        ),
        member_id=identity.member_id,
    )


@router.get("/module-runs/{run_id}/scenario-overlays")
def list_scenario_overlays(
    run_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    _require_run_kp(run_id, identity, repo)
    return repo.list_scenario_contract_overlays(run_id)


@router.post("/scenario-overlays/{overlay_id}/approve")
def approve_scenario_overlay(
    overlay_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    overlay = repo.get_scenario_contract_overlay(overlay_id)
    _require_run_kp(str(overlay["run_id"]), identity, repo)
    return ScenarioOverlayService(repo).approve(
        overlay_id, reviewed_by_member_id=identity.member_id
    )


@router.post("/module-runs/{run_id}/scene-transitions")
def transition_module_run_scene(
    run_id: str,
    payload: ModuleSceneTransition,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_run_kp(run_id, identity, repo)
    return ModuleRunService(repo).transition_scene(
        run_id,
        SceneTransitionCommand(
            expected_version=payload.expected_version,
            scene_key=payload.scene_key,
            scene_title=payload.scene_title,
            play_pace=payload.play_pace,
            location_entity_id=payload.location_entity_id,
            world_time=payload.world_time,
            note=payload.note,
        ),
        member_id=identity.member_id,
    )


@router.patch("/module-runs/{run_id}/entities/{entity_id}/state")
def update_module_run_entity_state(
    run_id: str,
    entity_id: str,
    payload: ModuleRunEntityStateUpdate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_run_kp(run_id, identity, repo)
    return ModuleRunService(repo).set_entity_state(
        run_id,
        entity_id,
        EntityStateCommand(
            expected_version=payload.expected_version,
            status=payload.status,
            note=payload.note,
        ),
        member_id=identity.member_id,
    )


@router.post("/module-runs/{run_id}/director/analyze")
def analyze_module_run_intent(
    run_id: str,
    payload: DirectorAnalysisRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_run_kp(run_id, identity, repo)
    return ModuleRunService(repo).analyze(run_id, payload.player_intent)


@router.post(
    "/module-runs/{run_id}/director/help",
    response_model=DirectorHelpResponse,
)
async def get_human_kp_help(
    run_id: str,
    payload: DirectorHelpRequest,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    """Return source-cited advice; never execute the suggested action."""

    run = _require_run_kp(run_id, identity, repo)
    gate = getattr(request.app.state, "director_help_call_gate", None)
    if not isinstance(gate, DirectorHelpCallGate):
        raise TypeError("Invalid Need Help call gate")
    started_at = perf_counter()
    attempt = await _begin_help_attempt(
        settings,
        campaign_id=str(run["campaign_id"]),
        run_id=run_id,
        requested_by_member_id=identity.member_id,
        request_id=str(request.state.request_id),
        question=payload.question,
        started_at=started_at,
    )
    terminal = _DirectorHelpTerminalLatch(settings, attempt, started_at)
    try:
        async with gate.reserve(run_id):
            return await _run_reserved_director_help(
                run_id=run_id,
                question=payload.question,
                request=request,
                repo=repo,
                settings=settings,
                terminal=terminal,
            )
    except (DirectorHelpRunInProgressError, DirectorHelpCapacityExceededError) as exc:
        _raise_after_audit(
            await terminal.terminate(
                outcome="rejected_busy",
                error_code=exc.code,
            )
        )
        raise
    except asyncio.CancelledError:
        result = (
            await terminal.drain()
            if terminal.started
            else await terminal.terminate(
                outcome="failed",
                error_code="request_cancelled",
            )
        )
        _log_cancelled_audit_error(
            result,
            "Need Help cancellation audit could not be settled",
        )
        raise


@router.post("/module-runs/{run_id}/director/world-expansion-proposals")
async def create_world_expansion_proposal(
    run_id: str,
    payload: WorldExpansionProposalRequest,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    run = _require_run_kp(run_id, identity, repo)
    if payload.pc_id:
        require_approved_pc_binding(repo, str(run["campaign_id"]), payload.pc_id)
    try:
        command = WorldExpansionCommand(
            run_id=run_id,
            player_intent=payload.player_intent,
            requested_expansion_kind=payload.requested_expansion_kind,
            pc_id=payload.pc_id,
            map_id=payload.map_id,
            setting_pack_id=payload.setting_pack_id,
            settlement_kind=payload.settlement_kind,
        )
        director = create_kp_orchestrator(repo, settings, request)
        if payload.auto_materialize:
            return (
                await AutoWorldExpansionService(repo).create_and_maybe_materialize(
                    command,
                    identity,
                    director,
                    source_model=settings.llm_model,
                )
            ).as_dict()
        return await TurnService(repo).create_world_expansion_proposal(
            command,
            identity,
            director,
            source_model=settings.llm_model,
        )
    except StructuredOutputError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Local model returned invalid world expansion output: {exc}",
        ) from exc
    except KpSessionEndedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
