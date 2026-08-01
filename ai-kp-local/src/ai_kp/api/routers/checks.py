from fastapi import APIRouter, Depends, Request

from ai_kp.api.authz import require_approved_pc_binding, require_campaign_role
from ai_kp.api.dependencies import get_app_settings, get_identity, get_repo
from ai_kp.api.llm import create_kp_orchestrator
from ai_kp.api.schemas import (
    OpposedCheckCreate,
    SkillCheckCreate,
    SkillCheckDecision,
    SkillCheckOverride,
    SkillCheckResolve,
)
from ai_kp.application.auto_turn_service import AutoTurnService
from ai_kp.application.check_service import (
    CheckService,
    CreateCheckCommand,
    CreateOpposedCheckCommand,
    OpposedSideCommand,
    ResolveCheckCommand,
)
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter()


def _opposed_side(payload) -> OpposedSideCommand:
    return OpposedSideCommand(
        skill_name=payload.skill_name,
        target=payload.target,
        bonus_dice=payload.bonus_dice,
        hidden=payload.hidden,
        visibility=payload.visibility,
        roller_member_id=payload.roller_member_id,
        pc_id=payload.pc_id,
    )


@router.post("/campaigns/{campaign_id}/checks")
def create_skill_check(
    campaign_id: str,
    payload: SkillCheckCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    effective_pc_id = payload.pc_id
    owner_profile_id = None
    if payload.roller_member_id:
        roller = repo.get_session_member(payload.roller_member_id)
        effective_pc_id = effective_pc_id or roller.get("pc_id")
        owner_profile_id = roller.get("player_profile_id")
    if effective_pc_id:
        require_approved_pc_binding(
            repo,
            campaign_id,
            effective_pc_id,
            owner_profile_id=owner_profile_id,
        )
    return CheckService(repo).create(
        campaign_id,
        identity,
        CreateCheckCommand(
            skill_name=payload.skill_name,
            difficulty=payload.difficulty,
            bonus_dice=payload.bonus_dice,
            hidden=payload.hidden,
            visibility=payload.visibility,
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


@router.post("/campaigns/{campaign_id}/opposed-checks")
def create_opposed_check(
    campaign_id: str,
    payload: OpposedCheckCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    for side in (payload.left, payload.right):
        if side.pc_id:
            owner_profile_id = None
            if side.roller_member_id:
                owner_profile_id = repo.get_session_member(
                    side.roller_member_id
                ).get("player_profile_id")
            require_approved_pc_binding(
                repo, campaign_id, side.pc_id, owner_profile_id=owner_profile_id
            )
    return CheckService(repo).create_opposed(
        campaign_id,
        identity,
        CreateOpposedCheckCommand(
            left=_opposed_side(payload.left),
            right=_opposed_side(payload.right),
            proposal_id=payload.proposal_id,
            player_action_id=payload.player_action_id,
        ),
    )


@router.get("/campaigns/{campaign_id}/opposed-checks")
def list_opposed_checks(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    return CheckService(repo).list_opposed(campaign_id, identity)


@router.post("/opposed-checks/{opposed_check_id}/resolve")
def resolve_opposed_check(
    opposed_check_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CheckService(repo).resolve_opposed(opposed_check_id, identity)


@router.post("/opposed-checks/{opposed_check_id}/reroll")
def reroll_opposed_check(
    opposed_check_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CheckService(repo).reroll_opposed(opposed_check_id, identity)


@router.get("/checks/{check_id}")
def get_skill_check(
    check_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CheckService(repo).get(check_id, identity)


@router.post("/checks/{check_id}/resolve")
async def resolve_skill_check(
    check_id: str,
    payload: SkillCheckResolve,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    resolved = CheckService(repo).resolve(
        check_id,
        identity,
        ResolveCheckCommand(
            input_method=payload.input_method,
            ones_digit=payload.ones_digit,
            tens_digits=tuple(payload.tens_digits),
        ),
    )
    if not payload.auto_advance:
        return resolved
    result = await AutoTurnService(repo).advance_after_check(
        check_id,
        director=create_kp_orchestrator(repo, settings, request),
        source_model=settings.llm_model,
    )
    if result is None:
        return resolved
    return result.as_dict()


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
