from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_identity, get_player_identity, get_repo
from ai_kp.api.schemas import (
    InvestigatorAssignment,
    InvestigatorCampaignStateUpdate,
    InvestigatorCreate,
    InvestigatorPermanentChangeCreate,
    InvestigatorPermanentChangeDecision,
    InvestigatorReview,
    InvestigatorSkillRecommendationRequest,
    InvestigatorSubmit,
    InvestigatorTimelineBranchCreate,
    PlayerProfileCreate,
)
from ai_kp.api.uploads import read_limited_body, safe_upload_filename
from ai_kp.application.character_timeline_service import (
    CharacterTimelineService,
    PermanentChangeCommand,
    PermanentChangeDecisionCommand,
)
from ai_kp.application.investigator_service import (
    CreateInvestigatorCommand,
    InvestigatorService,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember, AuthenticatedPlayer
from ai_kp.rulesets import DEFAULT_RULESET_ID, get_ruleset
from ai_kp.rulesets.coc7.character.xlsx_import import MAX_XLSX_BYTES

router = APIRouter()


@router.get("/investigator-skills/catalog")
def get_investigator_skill_catalog(
    ruleset_id: str = Query(default=DEFAULT_RULESET_ID, max_length=120),
) -> list[dict]:
    return get_ruleset(ruleset_id).list_skill_catalog()


@router.post("/investigator-skills/recommend")
def recommend_investigator_skills(
    payload: InvestigatorSkillRecommendationRequest,
    ruleset_id: str = Query(default=DEFAULT_RULESET_ID, max_length=120),
) -> dict:
    return get_ruleset(ruleset_id).recommend_skill_points(**payload.model_dump())


@router.post("/player-profiles")
def create_player_profile(
    payload: PlayerProfileCreate,
    repo: Repository = Depends(get_repo),
) -> dict:
    return InvestigatorService(repo).create_profile(payload.display_name)


@router.get("/player-profile")
def get_player_profile(
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return repo.get_player_profile(player.profile_id)


@router.post("/investigator-imports/preview")
async def preview_investigator_excel(
    request: Request,
    x_file_name: str | None = Header(default=None),
    x_ruleset_id: str = Header(default=DEFAULT_RULESET_ID),
    _player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    filename = safe_upload_filename(x_file_name, default="character.xlsx")
    data = await read_limited_body(
        request,
        max_bytes=MAX_XLSX_BYTES,
        label="调查员 XLSX",
    )
    return await run_in_threadpool(
        InvestigatorService(repo, x_ruleset_id).preview_excel,
        data,
        filename,
    )


@router.post("/investigators/preview")
def preview_manual_investigator(
    payload: InvestigatorCreate,
    _player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return InvestigatorService(repo).preview_manual(payload.canonical_sheet)


@router.post("/investigators")
def create_investigator(
    payload: InvestigatorCreate,
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return InvestigatorService(repo).create_investigator(
        player.profile_id,
        CreateInvestigatorCommand(**payload.model_dump()),
    )


@router.get("/investigators")
def list_investigators(
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    return InvestigatorService(repo).list_investigators(player.profile_id)


@router.get("/investigators/{investigator_id}")
def get_investigator(
    investigator_id: str,
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return InvestigatorService(repo).get_investigator(player.profile_id, investigator_id)


@router.get("/investigators/{investigator_id}/revisions")
def list_investigator_revisions(
    investigator_id: str,
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    return InvestigatorService(repo).list_revisions(player.profile_id, investigator_id)


@router.post("/investigators/{investigator_id}/revisions")
def revise_investigator(
    investigator_id: str,
    payload: InvestigatorCreate,
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return InvestigatorService(repo).revise_investigator(
        player.profile_id,
        investigator_id,
        CreateInvestigatorCommand(**payload.model_dump()),
    )


@router.post("/campaigns/{campaign_id}/investigators/{investigator_id}/submit")
def submit_investigator_to_campaign(
    campaign_id: str,
    investigator_id: str,
    payload: InvestigatorSubmit,
    identity: AuthenticatedMember = Depends(get_identity),
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("player",))
    return InvestigatorService(repo).submit_to_campaign(
        campaign_id=campaign_id,
        investigator_id=investigator_id,
        revision_id=payload.revision_id,
        owner_profile_id=player.profile_id,
        member_id=identity.member_id,
        session_id=identity.session_id,
        timeline_branch_id=payload.timeline_branch_id,
    )


@router.post("/investigators/{investigator_id}/timeline-branches")
def create_investigator_timeline_branch(
    investigator_id: str,
    payload: InvestigatorTimelineBranchCreate,
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CharacterTimelineService(repo).create_branch(
        investigator_id,
        player.profile_id,
        payload.label,
    )


@router.get("/investigators/{investigator_id}/timeline")
def get_owned_investigator_timeline(
    investigator_id: str,
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CharacterTimelineService(repo).owner_timeline(
        investigator_id,
        player.profile_id,
    )


@router.get("/investigators/{investigator_id}/permanent-changes")
def list_owned_permanent_changes(
    investigator_id: str,
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    return CharacterTimelineService(repo).list_proposals(
        investigator_id,
        player.profile_id,
    )


@router.post("/investigator-permanent-changes/{proposal_id}/decision")
def decide_investigator_permanent_change(
    proposal_id: str,
    payload: InvestigatorPermanentChangeDecision,
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return CharacterTimelineService(repo).decide(
        proposal_id,
        player.profile_id,
        PermanentChangeDecisionCommand(**payload.model_dump()),
    )


@router.get("/campaigns/{campaign_id}/my-investigators")
def list_my_campaign_investigators(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("player",))
    return InvestigatorService(repo).list_player_campaign_investigators(
        campaign_id, player.profile_id
    )


@router.get("/campaigns/{campaign_id}/investigator-submissions")
def list_campaign_investigator_submissions(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return InvestigatorService(repo).list_kp_campaign_investigators(campaign_id)


@router.post("/campaigns/{campaign_id}/investigators/{investigator_id}/review")
def review_campaign_investigator(
    campaign_id: str,
    investigator_id: str,
    payload: InvestigatorReview,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return InvestigatorService(repo).review(
        campaign_id=campaign_id,
        investigator_id=investigator_id,
        action=payload.action,
        comment=payload.comment,
        kp_member_id=identity.member_id,
        session_id=identity.session_id,
    )


@router.post("/sessions/{session_id}/members/{member_id}/assign-investigator")
def assign_campaign_investigator(
    session_id: str,
    member_id: str,
    payload: InvestigatorAssignment,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    if identity.session_id != session_id or identity.role != "kp":
        raise HTTPException(status_code=403, detail="KP access required for this session")
    return InvestigatorService(repo).assign(
        session_id=session_id,
        member_id=member_id,
        investigator_id=payload.investigator_id,
    )


@router.get("/campaigns/{campaign_id}/investigators/public")
def list_public_campaign_investigators(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id)
    return InvestigatorService(repo).list_public(campaign_id)


@router.patch("/campaigns/{campaign_id}/investigators/{investigator_id}/state")
def update_campaign_investigator_state(
    campaign_id: str,
    investigator_id: str,
    payload: InvestigatorCampaignStateUpdate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    changes = payload.model_dump(exclude={"expected_version"}, exclude_unset=True)
    return InvestigatorService(repo).update_campaign_state(
        campaign_id=campaign_id,
        investigator_id=investigator_id,
        expected_version=payload.expected_version,
        changes=changes,
    )


@router.get(
    "/campaigns/{campaign_id}/investigators/{investigator_id}/timeline"
)
def get_campaign_investigator_timeline(
    campaign_id: str,
    investigator_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return CharacterTimelineService(repo).keeper_timeline(
        campaign_id,
        investigator_id,
    )


@router.post(
    "/campaigns/{campaign_id}/investigators/{investigator_id}/permanent-changes"
)
def propose_investigator_permanent_change(
    campaign_id: str,
    investigator_id: str,
    payload: InvestigatorPermanentChangeCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return CharacterTimelineService(repo).propose(
        campaign_id=campaign_id,
        investigator_id=investigator_id,
        member_id=identity.member_id,
        command=PermanentChangeCommand(**payload.model_dump()),
    )
