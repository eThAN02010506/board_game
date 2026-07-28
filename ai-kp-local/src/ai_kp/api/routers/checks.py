from fastapi import APIRouter, Depends

from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import (
    SkillCheckCreate,
    SkillCheckDecision,
    SkillCheckOverride,
    SkillCheckResolve,
)
from ai_kp.application.check_service import (
    CheckService,
    CreateCheckCommand,
    ResolveCheckCommand,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter()


@router.post("/campaigns/{campaign_id}/checks")
def create_skill_check(
    campaign_id: str,
    payload: SkillCheckCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CheckService(repo).create(
        campaign_id,
        identity,
        CreateCheckCommand(
            skill_name=payload.skill_name,
            difficulty=payload.difficulty,
            bonus_dice=payload.bonus_dice,
            hidden=payload.hidden,
            allow_push=payload.allow_push,
            roller_member_id=payload.roller_member_id,
            pc_id=payload.pc_id,
            target=payload.target,
            proposal_id=payload.proposal_id,
            player_action_id=payload.player_action_id,
        ),
    )


@router.get("/campaigns/{campaign_id}/checks")
def list_skill_checks(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    return CheckService(repo).list(campaign_id, identity)


@router.get("/checks/{check_id}")
def get_skill_check(
    check_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CheckService(repo).get(check_id, identity)


@router.post("/checks/{check_id}/resolve")
def resolve_skill_check(
    check_id: str,
    payload: SkillCheckResolve,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CheckService(repo).resolve(
        check_id,
        identity,
        ResolveCheckCommand(
            input_method=payload.input_method,
            ones_digit=payload.ones_digit,
            tens_digits=tuple(payload.tens_digits),
        ),
    )


@router.post("/checks/{check_id}/replay")
def replay_skill_check(
    check_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CheckService(repo).replay(check_id, identity)


@router.post("/checks/{check_id}/override")
def override_skill_check(
    check_id: str,
    payload: SkillCheckOverride,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CheckService(repo).override(
        check_id,
        identity,
        success_level=payload.success_level,
        passed=payload.passed,
        reason=payload.reason,
    )


@router.post("/checks/{check_id}/cancel")
def cancel_skill_check(
    check_id: str,
    payload: SkillCheckDecision,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CheckService(repo).cancel(check_id, identity, reason=payload.reason)


@router.post("/checks/{check_id}/push")
def push_skill_check(
    check_id: str,
    payload: SkillCheckDecision,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CheckService(repo).push(check_id, identity, reason=payload.reason)
