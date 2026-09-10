"""Role-safe table communication endpoints."""

from fastapi import APIRouter, Depends, Query

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import TableMessageCreateInput
from ai_kp.application.table_message_service import TableMessageService
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter(tags=["communications"])
_TABLE_ROLES = ("kp", "player", "observer")


@router.get("/campaigns/{campaign_id}/table-members")
def list_table_members(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, _TABLE_ROLES)
    return TableMessageService(repo).list_members(identity)


@router.get("/campaigns/{campaign_id}/messages")
def list_table_messages(
    campaign_id: str,
    before_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, _TABLE_ROLES)
    return TableMessageService(repo).list_messages(
        identity, before_id=before_id, limit=limit
    )


@router.post("/campaigns/{campaign_id}/messages")
def send_table_message(
    campaign_id: str,
    payload: TableMessageCreateInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, _TABLE_ROLES)
    return TableMessageService(repo).send(identity, **payload.model_dump())


__all__ = ["router"]
