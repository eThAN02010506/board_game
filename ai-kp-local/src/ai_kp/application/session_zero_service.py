"""Application boundary for Session 0 consent and immediate safety tools."""

from typing import Protocol

from ai_kp.platform.sessions.models import AuthenticatedMember
from ai_kp.platform.sessions.session_zero import (
    CampaignSetupConfig,
    SafetyResponse,
    SessionZeroPreferences,
)


class SessionZeroStore(Protocol):
    def get_campaign(self, campaign_id: str) -> dict: ...

    def create_session_zero_revision(self, **values) -> dict: ...

    def update_session_zero_preferences(self, **values) -> dict: ...

    def confirm_session_zero_revision(self, revision_id: str, **values) -> dict: ...

    def session_zero_projection(self, campaign_id: str, member_id: str) -> dict: ...

    def create_session_safety_event(self, **values) -> dict: ...

    def resolve_session_safety_event(self, event_id: str, **values) -> dict: ...


class SessionZeroService:
    def __init__(self, repo: SessionZeroStore):
        self.repo = repo

    def view(self, identity: AuthenticatedMember) -> dict:
        return self.repo.session_zero_projection(
            identity.campaign_id, identity.member_id
        )

    def save_config(
        self,
        identity: AuthenticatedMember,
        *,
        expected_version: int,
        config: CampaignSetupConfig,
    ) -> dict:
        if identity.role != "kp":
            raise PermissionError("KP access required to edit campaign setup")
        campaign = self.repo.get_campaign(identity.campaign_id)
        if config.ruleset_id != campaign["ruleset_id"]:
            raise ValueError("Session 0 ruleset must match the pinned campaign ruleset")
        if config.ruleset_version != campaign["ruleset_version"]:
            raise ValueError("Session 0 ruleset version must match the campaign pin")
        self.repo.create_session_zero_revision(
            campaign_id=identity.campaign_id,
            config=config,
            expected_version=expected_version,
            actor_member_id=identity.member_id,
        )
        return self.view(identity)

    def save_preferences(
        self,
        identity: AuthenticatedMember,
        *,
        expected_version: int,
        preferences: SessionZeroPreferences,
    ) -> dict:
        self.repo.update_session_zero_preferences(
            campaign_id=identity.campaign_id,
            expected_version=expected_version,
            preferences=preferences,
            actor_member_id=identity.member_id,
        )
        return self.view(identity)

    def confirm(
        self,
        identity: AuthenticatedMember,
        *,
        revision_id: str,
        expected_version: int,
    ) -> dict:
        self.repo.confirm_session_zero_revision(
            revision_id,
            actor_member_id=identity.member_id,
            expected_version=expected_version,
        )
        return self.view(identity)

    def trigger_safety(
        self, identity: AuthenticatedMember, *, response_kind: SafetyResponse
    ) -> dict:
        # Deliberately accepts no explanation field. The durable event and model
        # logs therefore cannot retain a private reason by accident.
        event = self.repo.create_session_safety_event(
            actor_member_id=identity.member_id,
            response_kind=response_kind,
        )
        return {"event": self._public_safety_event(event), "session_zero": self.view(identity)}

    def resolve_safety(
        self,
        identity: AuthenticatedMember,
        *,
        event_id: str,
        resolution_kind: str,
    ) -> dict:
        event = self.repo.resolve_session_safety_event(
            event_id,
            actor_member_id=identity.member_id,
            resolution_kind=resolution_kind,
        )
        return {"event": self._public_safety_event(event), "session_zero": self.view(identity)}

    @staticmethod
    def _public_safety_event(event: dict) -> dict:
        return {
            "id": event["id"],
            "status": event["status"],
            "response_kind": event["response_kind"],
            "public_message": event["public_message"],
            "created_at": event["created_at"],
            "resolved_at": event.get("resolved_at"),
            "resolution_kind": event.get("resolution_kind"),
        }


__all__ = ["SessionZeroService", "SessionZeroStore"]
