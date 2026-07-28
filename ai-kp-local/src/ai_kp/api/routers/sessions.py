from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ai_kp.api.authz import is_local_admin, require_campaign_role
from ai_kp.api.dependencies import (
    get_identity,
    get_optional_identity,
    get_optional_player_identity,
    get_player_identity,
    get_repo,
)
from ai_kp.api.schemas import (
    SessionCreate,
    SessionJoin,
    SessionKpCredentialRecovery,
    SessionMemberPcAssign,
    SessionSeatClaim,
    SessionSeatCreate,
    SessionSeatPcAssign,
)
from ai_kp.application.session_service import SessionService
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember, AuthenticatedPlayer

router = APIRouter()


def _require_session_kp(identity: AuthenticatedMember, session_id: str) -> None:
    if identity.session_id != session_id or identity.role != "kp":
        raise HTTPException(status_code=403, detail="KP access required for this session")


@router.post("/campaigns/{campaign_id}/sessions")
def create_campaign_session(
    campaign_id: str,
    payload: SessionCreate,
    request: Request,
    x_ai_kp_admin_token: str | None = Header(default=None),
    identity: AuthenticatedMember | None = Depends(get_optional_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    if identity is not None:
        require_campaign_role(identity, campaign_id, ("kp",))
    elif not is_local_admin(request, x_ai_kp_admin_token):
        raise HTTPException(status_code=403, detail="KP or local administrator required")
    return SessionService(repo).create(
        campaign_id,
        title=payload.title,
        kp_display_name=payload.kp_display_name,
    )


@router.post("/sessions/join")
def join_campaign_session(
    payload: SessionJoin,
    repo: Repository = Depends(get_repo),
) -> dict:
    return SessionService(repo).join(
        payload.join_code,
        display_name=payload.display_name,
        pc_id=payload.pc_id,
    )


@router.post("/campaigns/{campaign_id}/sessions/recover-kp")
def recover_campaign_kp_credential(
    campaign_id: str,
    payload: SessionKpCredentialRecovery,
    request: Request,
    x_ai_kp_admin_token: str | None = Header(default=None),
    repo: Repository = Depends(get_repo),
) -> dict:
    if not is_local_admin(request, x_ai_kp_admin_token):
        raise HTTPException(status_code=403, detail="Local administrator access required")
    return SessionService(repo).recover_kp(
        campaign_id,
        display_name=payload.kp_display_name,
    )


@router.get("/auth/me")
def auth_me(identity: AuthenticatedMember = Depends(get_identity)) -> dict:
    return identity.__dict__


@router.get("/sessions/{session_id}")
def get_campaign_session(
    session_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    if identity.session_id != session_id:
        raise HTTPException(status_code=403, detail="Session token belongs to another session")
    return repo.get_campaign_session(session_id)


@router.get("/sessions/{session_id}/members")
def list_session_members(
    session_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    if identity.session_id != session_id or identity.role != "kp":
        raise HTTPException(status_code=403, detail="KP access required for this session")
    return repo.list_session_members(session_id)


@router.post("/sessions/{session_id}/members/{member_id}/revoke")
def revoke_session_member(
    session_id: str,
    member_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    if identity.session_id != session_id or identity.role != "kp":
        raise HTTPException(status_code=403, detail="KP access required for this session")
    return SessionService(repo).revoke_member_and_rotate_code(session_id, member_id)


@router.post("/sessions/{session_id}/members/{member_id}/assign-pc")
def assign_session_member_pc(
    session_id: str,
    member_id: str,
    payload: SessionMemberPcAssign,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    if identity.session_id != session_id or identity.role != "kp":
        raise HTTPException(status_code=403, detail="KP access required for this session")
    return SessionService(repo).assign_member_pc(session_id, member_id, payload.pc_id)


@router.post("/sessions/{session_id}/rotate-join-code")
def rotate_session_join_code(
    session_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    if identity.session_id != session_id or identity.role != "kp":
        raise HTTPException(status_code=403, detail="KP access required for this session")
    return SessionService(repo).rotate_join_code(session_id)


@router.post("/sessions/{session_id}/close")
def close_campaign_session(
    session_id: str,
    request: Request,
    x_ai_kp_admin_token: str | None = Header(default=None),
    identity: AuthenticatedMember | None = Depends(get_optional_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    repo.get_campaign_session(session_id)
    authorized_kp = (
        identity is not None and identity.session_id == session_id and identity.role == "kp"
    )
    if not authorized_kp and not is_local_admin(request, x_ai_kp_admin_token):
        raise HTTPException(status_code=403, detail="KP or local administrator required")
    return SessionService(repo).close(session_id)


@router.post("/sessions/{session_id}/seats")
def create_session_seat(
    session_id: str,
    payload: SessionSeatCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_session_kp(identity, session_id)
    return SessionService(repo).create_seat(
        session_id,
        label=payload.label,
        kp_member_id=identity.member_id,
        pc_id=payload.pc_id,
    )


@router.get("/sessions/{session_id}/seats")
def list_session_seats(
    session_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    _require_session_kp(identity, session_id)
    return repo.list_session_seats(session_id)


@router.post("/sessions/{session_id}/seats/{seat_id}/reissue")
def reissue_session_seat_invitation(
    session_id: str,
    seat_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_session_kp(identity, session_id)
    return SessionService(repo).reissue_seat_invitation(session_id, seat_id)


@router.post("/sessions/{session_id}/seats/{seat_id}/revoke")
def revoke_session_seat(
    session_id: str,
    seat_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_session_kp(identity, session_id)
    return SessionService(repo).revoke_seat(session_id, seat_id)


@router.patch("/sessions/{session_id}/seats/{seat_id}/pc")
def assign_session_seat_pc(
    session_id: str,
    seat_id: str,
    payload: SessionSeatPcAssign,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_session_kp(identity, session_id)
    return SessionService(repo).assign_seat_pc(session_id, seat_id, payload.pc_id)


@router.post("/session-seats/claim")
def claim_session_seat(
    payload: SessionSeatClaim,
    player: AuthenticatedPlayer | None = Depends(get_optional_player_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return SessionService(repo).claim_seat(
        payload.invitation_code,
        display_name=payload.display_name,
        player=player,
    )


@router.get("/player-profile/session-seats")
def list_player_session_seats(
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    return SessionService(repo).list_player_seats(player)


@router.post("/session-seats/{seat_id}/recover")
def recover_session_seat(
    seat_id: str,
    player: AuthenticatedPlayer = Depends(get_player_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return SessionService(repo).recover_seat(seat_id, player)
