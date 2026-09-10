"""HTTP transport for the authoritative multiplayer action workflow."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_app_settings, get_identity, get_repo
from ai_kp.api.llm import create_kp_orchestrator
from ai_kp.api.schemas import (
    ParallelActionAttentionRequest,
    ParallelActionCommitRequest,
    ParallelActionCoordinatorSummaryResponse,
    ParallelActionPlayerBatchResponse,
    ParallelActionPlayerRegatherResponse,
    ParallelActionSettlementRequest,
)
from ai_kp.application.auto_kp_queue_service import AutoKpQueueService
from ai_kp.application.parallel_action_player_projection import (
    ParallelActionPlayerProjectionService,
)
from ai_kp.application.parallel_action_regather_service import (
    ParallelActionRegatherCoordinator,
)
from ai_kp.application.parallel_action_workflow_service import (
    ParallelActionWorkflowService,
)
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter()


@router.post("/campaigns/{campaign_id}/actions/settle")
async def prepare_parallel_player_actions(
    campaign_id: str,
    payload: ParallelActionSettlementRequest,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    """Give each simultaneous action an independent, consent-bound ruling."""

    require_campaign_role(identity, campaign_id, ("kp",))
    if len(set(payload.action_ids)) != len(payload.action_ids):
        raise HTTPException(
            status_code=422,
            detail="Parallel actions must contain distinct action IDs",
        )
    actions = tuple(
        sorted(
            (repo.get_player_action(action_id) for action_id in payload.action_ids),
            key=lambda action: (str(action.get("created_at") or ""), str(action["id"])),
        )
    )
    run = repo.get_active_campaign_module_run(campaign_id)
    run_basis = (
        f"{run['id']}:{run['version']}" if run is not None else "no-active-run"
    )
    action_basis = "\n".join(str(action["id"]) for action in actions)
    digest = sha256(
        f"{campaign_id}\n{identity.session_id}\n{run_basis}\n{action_basis}".encode()
    ).hexdigest()
    configuration = repo.get_model_configuration()
    profile = (
        "large"
        if configuration and configuration.get("semantic_profile") == "large"
        else "small"
    )
    result = await ParallelActionWorkflowService(repo).prepare(
        actions,
        identity,
        create_kp_orchestrator(repo, settings, request),
        idempotency_key=f"manual-parallel:{digest}",
        source_model=settings.llm_model,
        profile=profile,
    )
    return result.as_dict()


@router.get(
    "/campaigns/{campaign_id}/parallel-action-batches/current",
    response_model=ParallelActionPlayerBatchResponse | None,
    response_model_exclude_unset=True,
)
def get_current_player_parallel_batch(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
):
    require_campaign_role(identity, campaign_id, ("player",))
    projection = ParallelActionPlayerProjectionService(repo).current(identity)
    return projection.as_dict() if projection is not None else None


@router.get(
    "/campaigns/{campaign_id}/parallel-action-regathers/current",
    response_model=ParallelActionPlayerRegatherResponse | None,
)
def get_current_player_parallel_regather(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
):
    require_campaign_role(identity, campaign_id, ("player",))
    projection = ParallelActionRegatherCoordinator(repo).current_projection(identity)
    return projection.as_dict() if projection is not None else None


@router.get(
    "/campaigns/{campaign_id}/parallel-action-batches",
    response_model=list[ParallelActionCoordinatorSummaryResponse],
)
def list_parallel_action_batches_needing_attention(
    campaign_id: str,
    status: Literal["needs_attention"] = Query(default="needs_attention"),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    """List only the safe metadata a KP needs to resume paused batches."""

    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.list_parallel_action_batch_coordinator_summaries(
        campaign_id,
        identity.session_id,
        status=status,
    )


@router.get("/parallel-action-batches/{batch_id}")
def get_parallel_action_batch(
    batch_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    batch = repo.get_parallel_action_batch(batch_id)
    require_campaign_role(identity, str(batch["campaign_id"]))
    if str(batch["session_id"]) != identity.session_id:
        raise HTTPException(status_code=404, detail="Parallel batch not found")
    if identity.role == "player":
        return ParallelActionPlayerProjectionService(repo).get(
            batch_id, identity
        ).as_dict()
    return batch


@router.post("/parallel-action-batches/{batch_id}/commit")
def commit_parallel_action_batch(
    batch_id: str,
    payload: ParallelActionCommitRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_batch_kp(repo, batch_id, identity)
    return ParallelActionWorkflowService(repo).commit(
        batch_id,
        expected_version=payload.expected_version,
    ).as_dict()


@router.post("/parallel-action-batches/{batch_id}/resume")
def resume_parallel_action_batch(
    batch_id: str,
    payload: ParallelActionAttentionRequest,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_batch_kp(repo, batch_id, identity)
    result = ParallelActionWorkflowService(repo).resume_needs_attention(
        batch_id,
        expected_version=payload.expected_version,
        identity=identity,
        reason=payload.reason,
    )
    job = AutoKpQueueService(repo).enqueue_parallel_followup(batch_id)
    if job is not None:
        _wake_auto_kp_worker(request)
    response = result.as_dict()
    response["job"] = job
    return response


@router.post("/parallel-action-batches/{batch_id}/abandon")
def abandon_parallel_action_batch(
    batch_id: str,
    payload: ParallelActionAttentionRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_batch_kp(repo, batch_id, identity)
    return ParallelActionWorkflowService(repo).abandon_needs_attention(
        batch_id,
        expected_version=payload.expected_version,
        identity=identity,
        reason=payload.reason,
    ).as_dict()


def _require_batch_kp(
    repo: Repository,
    batch_id: str,
    identity: AuthenticatedMember,
) -> dict:
    batch = repo.get_parallel_action_batch(batch_id)
    require_campaign_role(identity, str(batch["campaign_id"]), ("kp",))
    if str(batch["session_id"]) != identity.session_id:
        raise HTTPException(status_code=404, detail="Parallel batch not found")
    return batch


def _wake_auto_kp_worker(request: Request) -> None:
    worker = getattr(request.app.state, "auto_kp_worker", None)
    if worker is not None:
        worker.wake()


__all__ = ["router"]
