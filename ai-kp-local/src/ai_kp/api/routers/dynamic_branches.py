"""KP-only inspection and progression of approved dynamic branches."""

from typing import Literal

from fastapi import APIRouter, Depends, Query

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.dynamic_branch_schemas import (
    DynamicBranchAbandon,
    DynamicBranchBeatResolution,
    DynamicBranchResume,
)
from ai_kp.application.dynamic_branch_service import (
    AbandonDynamicBranchCommand,
    DynamicBranchService,
    ResolveBranchBeatCommand,
    ResumeDynamicBranchCommand,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter(tags=["dynamic-branches"])


@router.get("/campaigns/{campaign_id}/dynamic-branches")
def list_dynamic_branches(
    campaign_id: str,
    status: Literal[
        "approved",
        "active",
        "paused",
        "completed",
        "abandoned",
    ]
    | None = Query(default=None),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return DynamicBranchService(repo).list_for_campaign(
        campaign_id,
        identity,
        status=status,
    )


@router.get("/dynamic-branches/{branch_id}")
def get_dynamic_branch(
    branch_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return DynamicBranchService(repo).get(branch_id, identity)


@router.post("/dynamic-branches/{branch_id}/beats/resolve")
def resolve_dynamic_branch_beat(
    branch_id: str,
    payload: DynamicBranchBeatResolution,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return DynamicBranchService(repo).resolve_beat(
        branch_id,
        identity,
        ResolveBranchBeatCommand(
            expected_version=payload.expected_version,
            command_id=payload.command_id,
            outcome=payload.outcome,
            note=payload.note,
            observed_effects=tuple(payload.observed_effects),
        ),
    )


@router.post("/dynamic-branches/{branch_id}/resume")
def resume_dynamic_branch(
    branch_id: str,
    payload: DynamicBranchResume,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return DynamicBranchService(repo).resume(
        branch_id,
        identity,
        ResumeDynamicBranchCommand(
            expected_version=payload.expected_version,
            command_id=payload.command_id,
            note=payload.note,
        ),
    )


@router.post("/dynamic-branches/{branch_id}/abandon")
def abandon_dynamic_branch(
    branch_id: str,
    payload: DynamicBranchAbandon,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return DynamicBranchService(repo).abandon(
        branch_id,
        identity,
        AbandonDynamicBranchCommand(
            expected_version=payload.expected_version,
            command_id=payload.command_id,
            note=payload.note,
        ),
    )


__all__ = ["router"]
