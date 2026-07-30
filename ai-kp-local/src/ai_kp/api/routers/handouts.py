"""Player handouts and clue reveal endpoints."""

from fastapi import APIRouter, Depends

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import HandoutCreate, HandoutUpdate
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter(tags=["handouts"])


def _player_projection(handout: dict, member_id: str) -> dict:
    return {
        key: value
        for key, value in handout.items()
        if key not in {"created_by_member_id", "revealed_by_member_id", "read_receipts"}
    } | {
        "read": any(item["member_id"] == member_id for item in handout["read_receipts"])
    }


@router.post("/campaigns/{campaign_id}/handouts")
def create_handout(
    campaign_id: str,
    payload: HandoutCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.create_handout(
        campaign_id=campaign_id,
        member_id=identity.member_id,
        **payload.model_dump(),
    )


@router.get("/campaigns/{campaign_id}/handouts")
def list_handouts(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp", "player"))
    rows = repo.list_handouts(campaign_id, include_drafts=identity.role == "kp")
    return rows if identity.role == "kp" else [
        _player_projection(row, identity.member_id) for row in rows
    ]


@router.get("/campaigns/{campaign_id}/handout-link-options")
def list_handout_link_options(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.list_handout_link_options(campaign_id)


@router.patch("/handouts/{handout_id}")
def update_handout(
    handout_id: str,
    payload: HandoutUpdate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    handout = repo.get_handout(handout_id)
    require_campaign_role(identity, str(handout["campaign_id"]), ("kp",))
    return repo.update_handout(
        handout_id, member_id=identity.member_id, **payload.model_dump()
    )


@router.put("/handouts/{handout_id}/read")
def mark_handout_read(
    handout_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    handout = repo.get_handout(handout_id)
    require_campaign_role(identity, str(handout["campaign_id"]), ("player",))
    return _player_projection(
        repo.mark_handout_read(handout_id, identity.member_id), identity.member_id
    )
