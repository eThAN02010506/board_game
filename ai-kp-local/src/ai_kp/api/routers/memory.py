"""Campaign-scoped investigator memory timeline endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ai_kp.api.authz import require_approved_pc_binding, require_campaign_role
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import MemoryCurationCreate
from ai_kp.application.memory_timeline_service import (
    CurateMemoryCommand,
    MemoryTimelineService,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter()


@router.get("/campaigns/{campaign_id}/memory/timeline")
def list_memory_timeline(
    campaign_id: str,
    pc_id: str | None = None,
    classification: Literal["major", "side", "npc", "clue", "other"] | None = None,
    q: str | None = Query(default=None, max_length=500),
    include_hidden: bool = False,
    limit: int = Query(default=100, ge=1, le=250),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id)
    view = "kp"
    if identity.role == "player":
        view = "player"
        include_hidden = False
        if pc_id and pc_id != identity.pc_id:
            raise HTTPException(
                status_code=403,
                detail="Players can only read their own investigator timeline",
            )
        binding = require_approved_pc_binding(
            repo,
            campaign_id,
            identity.pc_id,
            owner_profile_id=identity.player_profile_id,
        )
        pc_id = str(binding["legacy_pc_id"])
    return MemoryTimelineService(repo).list_timeline(
        campaign_id,
        pc_id=pc_id,
        view=view,
        classification=classification,
        query=q,
        include_hidden=include_hidden,
        limit=limit,
    )


@router.post("/campaigns/{campaign_id}/memories/{memory_id}/curation")
def curate_memory(
    campaign_id: str,
    memory_id: str,
    payload: MemoryCurationCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return MemoryTimelineService(repo).curate(
        campaign_id,
        memory_id,
        session_id=identity.session_id,
        member_id=identity.member_id,
        command=CurateMemoryCommand(**payload.model_dump()),
    )
