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
        effect_ceiling: str | None = None,
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


class EncounterIntentDirector(Protocol):
    async def propose_encounter_action(
        self,
        *,
        campaign_id: str,
        snapshot: dict,
        player_action: str,
    ) -> Any: ...


class EnemyTurnDirector(Protocol):
    async def select_enemy_turn(
        self,
        *,
        campaign_id: str,
        snapshot: dict,
    ) -> Any: ...


class DirectorHelpDirector(Protocol):
    """Read-only model capability used by an explicitly requesting human KP."""

    async def advise_human_kp(
        self,
        *,
        campaign_id: str,
        brief: Any,
    ) -> Any: ...


class KernelDirector(Protocol):
    """Constrained model capabilities used around the authoritative kernel."""

    async def interpret_tabletop_turn(
        self,
        *,
        campaign_id: str,
        contract: Any,
        snapshot: Any,
        player_action: str,
    ) -> Any: ...

    async def respond_tabletop_turn(
        self,
        *,
        campaign_id: str,
        contract: Any,
        snapshot: Any,
        player_action: str,
        frame: Any,
        route: str,
    ) -> Any: ...

    async def select_kernel_action(
        self,
        *,
        campaign_id: str,
        contract: Any,
        snapshot: Any,
        player_action: str,
        profile: str,
    ) -> Any: ...

    async def author_kernel_world_expansion(
        self,
        *,
        campaign_id: str,
        contract: Any,
        snapshot: Any,
        player_action: str,
        proposal_id: str,
        tabletop_frame: Any | None = None,
    ) -> Any: ...

    async def narrate_kernel_action(
        self,
        *,
        campaign_id: str,
        contract: Any,
        preview: Any,
        snapshot: Any,
        player_action: str,
    ) -> Any: ...


__all__ = [
    "CheckConsequenceDirector",
    "DirectorHelpDirector",
    "EncounterIntentDirector",
    "EnemyTurnDirector",
    "KernelDirector",
    "KpDirector",
    "SessionRecapDirector",
    "WorldExpansionDirector",
]
