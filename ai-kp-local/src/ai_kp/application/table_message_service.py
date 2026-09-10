"""Deterministic authority for table, party, announcement, and direct messages."""

from __future__ import annotations

from typing import Any, Protocol

from ai_kp.platform.sessions.models import AuthenticatedMember
from ai_kp.platform.sessions.session_zero import CampaignSetupConfig


class TableMessageStore(Protocol):
    def get_session_member(self, member_id: str) -> dict: ...

    def get_campaign_session(self, session_id: str) -> dict: ...

    def get_current_session_zero_revision(self, campaign_id: str) -> dict | None: ...

    def list_safe_table_members(self, session_id: str) -> list[dict[str, Any]]: ...

    def create_table_message(self, **values: Any) -> dict[str, Any]: ...

    def list_visible_table_messages(self, identity: AuthenticatedMember, **values: Any) -> list[dict[str, Any]]: ...


class TableMessageService:
    def __init__(self, repo: TableMessageStore):
        self.repo = repo

    def list_members(self, identity: AuthenticatedMember) -> list[dict[str, Any]]:
        return self.repo.list_safe_table_members(identity.session_id)

    def list_messages(
        self, identity: AuthenticatedMember, *, before_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        return self.repo.list_visible_table_messages(
            identity, before_id=before_id, limit=limit
        )

    def send(
        self,
        identity: AuthenticatedMember,
        *,
        audience: str,
        content: str,
        recipient_member_id: str | None,
        client_message_id: str,
    ) -> dict[str, Any]:
        normalized = content.strip()
        if not normalized:
            raise ValueError("Message content is required")
        if audience == "announcement" and identity.role != "kp":
            raise PermissionError("Only the KP may send announcements")
        if audience in {"table", "party"} and identity.role == "observer":
            raise PermissionError("Observers have read-only table access")
        if audience != "direct" and recipient_member_id is not None:
            raise ValueError("Only direct messages have a recipient")
        if audience == "direct":
            self._authorize_direct(identity, recipient_member_id)
        return self.repo.create_table_message(
            identity=identity,
            audience=audience,
            content=normalized,
            recipient_member_id=recipient_member_id,
            client_message_id=client_message_id,
        )

    def _authorize_direct(
        self, identity: AuthenticatedMember, recipient_member_id: str | None
    ) -> None:
        if not recipient_member_id:
            raise ValueError("Direct messages require a recipient")
        if recipient_member_id == identity.member_id:
            raise ValueError("Direct messages cannot target the sender")
        recipient = self.repo.get_session_member(recipient_member_id)
        session = self.repo.get_campaign_session(identity.session_id)
        if (
            recipient["session_id"] != identity.session_id
            or recipient["campaign_id"] != identity.campaign_id
            or recipient.get("revoked_at") is not None
            or session["status"] != "active"
        ):
            raise KeyError("Direct-message recipient not found")
        if identity.role == "kp" or recipient["role"] == "kp":
            return
        if identity.role != "player" or recipient["role"] != "player":
            raise PermissionError("Observer direct messages may only target the KP")
        revision = self.repo.get_current_session_zero_revision(identity.campaign_id)
        allowed = bool(
            revision
            and CampaignSetupConfig.model_validate(revision["config"]).allow_player_whispers
        )
        if not allowed:
            raise PermissionError("Player-to-player whispers are disabled by Session 0")


__all__ = ["TableMessageService", "TableMessageStore"]
