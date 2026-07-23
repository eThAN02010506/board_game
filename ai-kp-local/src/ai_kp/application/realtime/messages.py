"""Pure parsing and response construction for the realtime wire protocol."""

from dataclasses import dataclass
from typing import Any

from ai_kp.platform.sessions.models import AuthenticatedMember


@dataclass(frozen=True)
class AuthenticationRequest:
    ticket: str
    after_cursor: str


@dataclass(frozen=True)
class ProtocolRejection:
    code: int
    reason: str


def parse_authentication(message: object) -> AuthenticationRequest | ProtocolRejection:
    if not isinstance(message, dict) or message.get("type") != "authenticate":
        return ProtocolRejection(4400, "Authentication frame required")
    ticket = message.get("ticket")
    if not isinstance(ticket, str) or not ticket:
        return ProtocolRejection(4401, "Invalid realtime ticket")
    requested_cursor = message.get("after_cursor")
    return AuthenticationRequest(
        ticket=ticket,
        after_cursor=requested_cursor if isinstance(requested_cursor, str) else "",
    )


def ready_message(
    identity: AuthenticatedMember,
    *,
    cursor_key: str,
    resync_required: bool,
) -> dict[str, Any]:
    return {
        "type": "realtime.ready",
        "session_id": identity.session_id,
        "campaign_id": identity.campaign_id,
        "member_id": identity.member_id,
        "role": identity.role,
        "cursor": cursor_key,
        "resync_required": resync_required,
    }


def client_response(message: object, *, cursor_key: str) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return {"type": "protocol.error", "detail": "Expected a JSON object"}
    if message.get("type") == "ping":
        return {"type": "pong", "cursor": cursor_key}
    if message.get("type") == "pong":
        return None
    return {"type": "protocol.error", "detail": "Unsupported client message"}
