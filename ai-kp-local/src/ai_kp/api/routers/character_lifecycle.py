"""Role-safe character lifecycle endpoints."""

from fastapi import APIRouter, Depends

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import (
    CharacterLifecycleDecisionInput,
    CharacterLifecycleProposalInput,
)
from ai_kp.application.character_lifecycle_service import (
    CharacterLifecycleService,
    LifecycleDecisionCommand,
    LifecycleProposalCommand,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter(tags=["character-lifecycle"])


@router.get("/campaigns/{campaign_id}/character-lifecycle")
def get_character_lifecycle(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp", "player", "observer"))
    return CharacterLifecycleService(repo).view(campaign_id, identity)


@router.post("/campaigns/{campaign_id}/character-lifecycle/requests")
def propose_character_lifecycle(
    campaign_id: str,
    payload: CharacterLifecycleProposalInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return CharacterLifecycleService(repo).propose(
        campaign_id,
        identity,
        LifecycleProposalCommand(**payload.model_dump()),
    )


@router.post("/character-lifecycle/requests/{request_id}/decision")
def decide_character_lifecycle(
    request_id: str,
    payload: CharacterLifecycleDecisionInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CharacterLifecycleService(repo).decide(
        request_id,
        identity,
        LifecycleDecisionCommand(**payload.model_dump()),
    )


__all__ = ["router"]
