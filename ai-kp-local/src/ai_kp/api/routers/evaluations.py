"""Replayable simulated-campaign evaluation endpoints."""

from fastapi import APIRouter, Depends

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import SimulationCaseCreate
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter(tags=["evaluations"])


@router.post("/campaigns/{campaign_id}/simulation-cases")
def create_case(
    campaign_id: str,
    payload: SimulationCaseCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.create_simulation_case(
        campaign_id, payload.name, payload.definition, identity.member_id
    )


@router.get("/campaigns/{campaign_id}/simulation-cases")
def list_cases(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.list_simulation_cases(campaign_id)


@router.post("/simulation-cases/{case_id}/runs")
def run_case(
    case_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    case = repo.get_simulation_case(case_id)
    require_campaign_role(identity, str(case["campaign_id"]), ("kp",))
    return repo.run_simulation_case(case_id)
