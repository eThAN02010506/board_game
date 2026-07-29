import ipaddress
import secrets

from fastapi import Header, HTTPException, Request

from ai_kp.api.dependencies import get_app_settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember


def is_local_admin(request: Request, supplied_admin_token: str | None) -> bool:
    settings = get_app_settings(request)
    if (
        settings.admin_token
        and supplied_admin_token
        and secrets.compare_digest(settings.admin_token, supplied_admin_token)
    ):
        return True
    if (
        settings.deployment_mode != "local"
        or not settings.local_admin_enabled
        or request.client is None
    ):
        return False
    try:
        address = ipaddress.ip_address(request.client.host)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            return address.ipv4_mapped.is_loopback
        return address.is_loopback
    except ValueError:
        return request.client.host == "localhost"


def require_local_admin(
    request: Request,
    x_ai_kp_admin_token: str | None = Header(default=None),
) -> None:
    if not is_local_admin(request, x_ai_kp_admin_token):
        raise HTTPException(status_code=403, detail="Local administrator access required")


def require_campaign_role(
    identity: AuthenticatedMember,
    campaign_id: str,
    roles: tuple[str, ...] = ("kp", "player"),
) -> None:
    if identity.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Campaign resource not found")
    if identity.role not in roles:
        raise HTTPException(status_code=403, detail=f"Required role: {', '.join(roles)}")


def require_approved_pc_binding(
    repo: Repository,
    campaign_id: str,
    pc_id: str | None,
    *,
    owner_profile_id: str | None = None,
) -> dict:
    """Fail closed unless a legacy PC id projects one KP-approved investigator."""

    if not pc_id:
        raise HTTPException(
            status_code=409,
            detail="An approved investigator must be bound before this action",
        )
    row = repo.connection.execute(
        """
        SELECT ci.investigator_id, ci.owner_profile_id, ci.approved_revision_id,
               ci.legacy_pc_id
        FROM campaign_investigators ci
        JOIN investigator_revisions ir
          ON ir.id = ci.approved_revision_id
         AND ir.investigator_id = ci.investigator_id
        JOIN investigator_campaign_state state
          ON state.campaign_id = ci.campaign_id
         AND state.investigator_id = ci.investigator_id
         AND state.approved_revision_id = ci.approved_revision_id
        WHERE ci.campaign_id = ? AND ci.legacy_pc_id = ?
          AND ci.approved_revision_id IS NOT NULL
        """,
        (campaign_id, pc_id),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=409,
            detail="This character is not a consistent KP-approved investigator binding",
        )
    if owner_profile_id is not None and row["owner_profile_id"] != owner_profile_id:
        raise HTTPException(status_code=403, detail="Investigator belongs to another player")
    return dict(row)


def campaign_for_map(repo: Repository, map_id: str) -> str:
    row = repo.connection.execute(
        "SELECT campaign_id FROM maps WHERE id = ?",
        (map_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Map not found: {map_id}")
    return str(row["campaign_id"])


def campaign_for_token(repo: Repository, token_id: str) -> str:
    row = repo.connection.execute(
        """
        SELECT m.campaign_id FROM map_tokens t JOIN maps m ON m.id = t.map_id
        WHERE t.id = ?
        """,
        (token_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Map token not found: {token_id}")
    return str(row["campaign_id"])


def campaign_for_proposal(repo: Repository, proposal_id: str) -> str:
    row = repo.connection.execute(
        "SELECT campaign_id FROM turn_proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Turn proposal not found: {proposal_id}")
    return str(row["campaign_id"])
