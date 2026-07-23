"""Stable authenticated identities shared by delivery, application, and adapters."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthenticatedMember:
    member_id: str
    session_id: str
    campaign_id: str
    role: str
    display_name: str
    pc_id: str | None
    player_profile_id: str | None = None
    seat_id: str | None = None


@dataclass(frozen=True)
class AuthenticatedPlayer:
    profile_id: str
    display_name: str


__all__ = ["AuthenticatedMember", "AuthenticatedPlayer"]
