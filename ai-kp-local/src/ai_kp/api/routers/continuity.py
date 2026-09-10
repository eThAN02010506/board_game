"""Role-projected Session End and Continue endpoints."""

from fastapi import APIRouter, Depends, Request

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import (
    CampaignContinueInput,
    CampaignEpisodeTransitionInput,
    CampaignObjectiveCreateInput,
    CampaignObjectiveUpdateInput,
    SessionEndInput,
)
from ai_kp.application.auto_kp_queue_service import AutoKpQueueService
from ai_kp.application.campaign_objective_service import (
    CampaignObjectiveService,
    CreateObjectiveCommand,
    UpdateObjectiveCommand,
)
from ai_kp.application.session_continuity_service import SessionContinuityService
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter(tags=["continuity"])
_ROLES = ("kp", "player", "observer")


@router.get("/campaigns/{campaign_id}/objectives")
def list_campaign_objectives(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, _ROLES)
    return CampaignObjectiveService(repo).list(campaign_id, identity)


@router.post("/campaigns/{campaign_id}/objectives")
def create_campaign_objective(
    campaign_id: str,
    payload: CampaignObjectiveCreateInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return CampaignObjectiveService(repo).create(
        campaign_id,
        identity,
        CreateObjectiveCommand(
            **payload.model_dump(exclude={"source_refs"}),
            source_refs=tuple(payload.source_refs),
        ),
    )


@router.post("/objectives/{objective_id}/commands")
def update_campaign_objective(
    objective_id: str,
    payload: CampaignObjectiveUpdateInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CampaignObjectiveService(repo).update(
        objective_id,
        identity,
        UpdateObjectiveCommand(
            **payload.model_dump(exclude={"source_refs"}),
            source_refs=tuple(payload.source_refs),
        ),
    )


@router.get("/campaigns/{campaign_id}/continuity")
def get_campaign_continuity(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, _ROLES)
    return SessionContinuityService(repo).view(identity)


@router.post("/campaigns/{campaign_id}/session-end")
def end_campaign_episode(
    campaign_id: str,
    payload: SessionEndInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return SessionContinuityService(repo).end(identity, **payload.model_dump())


@router.post("/campaigns/{campaign_id}/continue")
def continue_campaign(
    campaign_id: str,
    payload: CampaignContinueInput,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    result = SessionContinuityService(repo).continue_campaign(
        identity, **payload.model_dump()
    )
    _resume_encounter_automation(request, repo, identity)
    return result


@router.post("/campaigns/{campaign_id}/episode/transition")
def transition_campaign_episode(
    campaign_id: str,
    payload: CampaignEpisodeTransitionInput,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    result = SessionContinuityService(repo).transition(identity, **payload.model_dump())
    if payload.target == "in_progress":
        _resume_encounter_automation(request, repo, identity)
    return result


def _resume_encounter_automation(
    request: Request, repo: Repository, identity: AuthenticatedMember
) -> None:
    queued = False
    service = AutoKpQueueService(repo)
    for encounter in repo.list_coc7_encounters(
        identity.campaign_id, identity.session_id
    ):
        if encounter["status"] != "active":
            continue
        queued = service.enqueue_encounter_turn(str(encounter["id"])) is not None or queued
    if queued:
        request.app.state.auto_kp_worker.wake()


__all__ = ["router"]
