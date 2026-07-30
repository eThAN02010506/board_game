"""AI director port consumed by the turn use case."""

from typing import Any, Protocol


class KpDirector(Protocol):
    async def handle_player_action(
        self,
        campaign_id: str,
        player_action: str,
        pc_id: str | None = None,
        location: str | None = None,
        map_id: str | None = None,
        profession_hint: str | None = None,
        active_spoiler_tags: tuple[str, ...] = (),
    ) -> Any: ...


class CheckConsequenceDirector(Protocol):
    async def handle_check_consequence(
        self,
        *,
        campaign_id: str,
        player_action: str,
        check_snapshot: dict,
        pc_id: str | None = None,
        location: str | None = None,
        map_id: str | None = None,
    ) -> Any: ...


class WorldExpansionDirector(Protocol):
    async def handle_world_expansion(
        self,
        *,
        campaign_id: str,
        player_intent: str,
        analysis_snapshot: dict,
        pc_id: str | None = None,
        location: str | None = None,
        map_id: str | None = None,
        active_spoiler_tags: tuple[str, ...] = (),
    ) -> Any: ...


class SessionRecapDirector(Protocol):
    async def handle_session_recap(self, snapshot: dict) -> Any: ...


__all__ = [
    "CheckConsequenceDirector",
    "KpDirector",
    "SessionRecapDirector",
    "WorldExpansionDirector",
]
