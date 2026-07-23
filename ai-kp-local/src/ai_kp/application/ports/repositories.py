"""Narrow persistence ports for application services."""

from typing import Any, Protocol

from ai_kp.platform.scenes.map_generation import GeneratedMap


class CampaignStore(Protocol):
    def create_campaign(
        self,
        title: str,
        system: str = "coc7",
        current_time: str | None = None,
    ) -> dict: ...

    def get_campaign(self, campaign_id: str) -> dict: ...

    def list_campaigns(self) -> list[dict]: ...


class RealtimeOutbox(Protocol):
    def append_realtime_event(
        self,
        *,
        session_id: str,
        campaign_id: str,
        event_type: str,
        audience: str = "session",
        member_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class MapStore(RealtimeOutbox, Protocol):
    def create_map(
        self,
        campaign_id: str,
        generated_map: GeneratedMap,
        created_by: str = "ai",
    ) -> dict: ...

    def set_map_status(self, map_id: str, status: str) -> dict: ...

    def is_map_token_player_visible(self, token_id: str) -> bool: ...

    def place_map_token(
        self,
        map_id: str,
        label: str,
        location_name: str,
        actor_type: str = "pc",
        actor_id: str | None = None,
        visibility: str = "table",
        color: str = "#b93f2d",
    ) -> dict: ...

    def move_map_token(
        self,
        token_id: str,
        to_location_name: str,
        moved_by: str = "player",
        note: str = "",
        require_route: bool = True,
        allowed_visibility: tuple[str, ...] | None = None,
        expected_version: int | None = None,
    ) -> dict: ...


class CheckStore(CampaignStore, Protocol):
    def create_skill_check(
        self,
        *,
        campaign_id: str,
        session_id: str,
        requested_by_member_id: str,
        skill_name: str,
        difficulty: str,
        ruleset_id: str,
        ruleset_version: str,
        source_reference: dict[str, Any],
        bonus_dice: int = 0,
        hidden: bool = False,
        allow_push: bool = True,
        roller_member_id: str | None = None,
        pc_id: str | None = None,
        target: int | None = None,
        proposal_id: str | None = None,
        player_action_id: str | None = None,
        pushed_from_check_id: str | None = None,
    ) -> dict[str, Any]: ...

    def get_skill_check(self, check_id: str) -> dict[str, Any]: ...

    def list_skill_checks(
        self,
        campaign_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]: ...

    def resolve_skill_check(
        self,
        check_id: str,
        *,
        actor_member_id: str,
        input_method: str,
        resolution: dict[str, Any],
    ) -> dict[str, Any]: ...

    def override_skill_check(
        self,
        check_id: str,
        *,
        actor_member_id: str,
        success_level: str,
        passed: bool,
        reason: str,
    ) -> dict[str, Any]: ...

    def cancel_skill_check(
        self,
        check_id: str,
        *,
        actor_member_id: str,
        reason: str,
    ) -> dict[str, Any]: ...

    def push_skill_check(
        self,
        check_id: str,
        *,
        actor_member_id: str,
        reason: str,
    ) -> dict[str, Any]: ...


__all__ = ["CampaignStore", "CheckStore", "MapStore", "RealtimeOutbox"]
