from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ai_kp.api.authz import is_local_admin, require_local_admin
from ai_kp.api.dependencies import get_optional_identity, get_repo
from ai_kp.api.schemas import CampaignCreate
from ai_kp.application.campaign_service import CampaignService, CreateCampaignCommand
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter()


@router.get("/campaigns")
def list_campaigns(
    request: Request,
    x_ai_kp_admin_token: str | None = Header(default=None),
    identity: AuthenticatedMember | None = Depends(get_optional_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    service = CampaignService(repo)
    if identity is not None:
        return service.list_accessible(identity.campaign_id)
    if is_local_admin(request, x_ai_kp_admin_token):
        return service.list_accessible()
    raise HTTPException(status_code=401, detail="Session token required")


@router.post("/campaigns")
def create_campaign(
    payload: CampaignCreate,
    _admin: None = Depends(require_local_admin),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CampaignService(repo).create(CreateCampaignCommand(**payload.model_dump()))
