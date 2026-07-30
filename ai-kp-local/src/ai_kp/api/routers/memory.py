"""Campaign-scoped investigator memory timeline endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ai_kp.api.authz import require_approved_pc_binding, require_campaign_role
from ai_kp.api.dependencies import get_app_settings, get_identity, get_repo
from ai_kp.api.llm import create_kp_orchestrator
from ai_kp.api.schemas import MemoryCurationCreate, SessionRecapReview
from ai_kp.application.errors import KpSessionEndedError
from ai_kp.application.memory_timeline_service import (
    CurateMemoryCommand,
    MemoryTimelineService,
)
from ai_kp.application.session_recap_service import (
    ReviewRecapCandidateCommand,
    SessionRecapService,
)
from ai_kp.bootstrap.settings import Settings
from ai_kp.director.turn_output import StructuredOutputError
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter()


@router.post("/sessions/{session_id}/recaps/generate")
async def generate_session_recap(
    session_id: str,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    if identity.session_id != session_id or identity.role != "kp":
        raise HTTPException(status_code=403, detail="KP access required for this session")
    try:
        return await SessionRecapService(repo).generate(
            identity,
            create_kp_orchestrator(repo, settings, request),
            source_model=settings.llm_model,
        )
    except StructuredOutputError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Local model returned invalid session recap output: {exc}",
        ) from exc
    except KpSessionEndedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/recaps/latest")
def get_latest_session_recap(
    session_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict | None:
    if identity.session_id != session_id or identity.role != "kp":
        raise HTTPException(status_code=403, detail="KP access required for this session")
    return SessionRecapService(repo).latest(identity)


@router.post("/session-recap-candidates/{candidate_id}/review")
def review_session_recap_candidate(
    candidate_id: str,
    payload: SessionRecapReview,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    if identity.role != "kp":
        raise HTTPException(status_code=403, detail="KP access required")
    return SessionRecapService(repo).review(
        candidate_id,
        identity,
        ReviewRecapCandidateCommand(**payload.model_dump()),
    )


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
