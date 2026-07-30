"""Persistence contract for atomic world-expansion encounter projection."""

from typing import Any, Protocol

from ai_kp.application.ports.facts import FactStore
from ai_kp.application.ports.travel_graph import TravelGraphStore


class WorldExpansionMaterializationStore(FactStore, TravelGraphStore, Protocol):
    def begin_world_expansion_materialization(self) -> None: ...

    def finish_world_expansion_materialization(self) -> None: ...

    def rollback_world_expansion_materialization(self) -> None: ...

    def get_turn_proposal(self, proposal_id: str) -> dict: ...

    def add_proposal_action(
        self,
        proposal_id: str,
        action_type: str,
        actor: str = "human_kp",
        note: str = "",
        payload: dict | None = None,
    ) -> dict: ...

    def is_session_member_active(self, member_id: str, session_id: str) -> bool: ...

    def append_event(self, **values: Any) -> dict: ...

    def create_npc(self, **values: Any) -> dict: ...

    def get_npc(self, npc_id: str) -> dict: ...

    def get_campaign_npc(self, campaign_id: str, npc_id: str) -> dict: ...

    def link_npc_to_campaign(self, campaign_id: str, npc_id: str, **values: Any) -> None: ...

    def update_campaign_npc_last_seen(
        self,
        campaign_id: str,
        npc_id: str,
        happened_at: str | None,
    ) -> None: ...

    def require_approved_contact_investigators(
        self,
        campaign_id: str,
        investigator_ids: tuple[str, ...],
    ) -> list[dict]: ...

    def npc_is_linked_to_campaign(self, campaign_id: str, npc_id: str) -> bool: ...

    def npc_is_authorized_reappearance(
        self,
        campaign_id: str,
        npc_id: str,
        investigator_ids: tuple[str, ...],
    ) -> bool: ...

    def record_investigator_npc_encounter(self, **values: Any) -> dict: ...

    def get_campaign(self, campaign_id: str) -> dict: ...

    def get_npc_availability_profile(self, npc_id: str) -> dict | None: ...

    def get_campaign_npc_reappearance_policy(self, campaign_id: str) -> dict: ...

    def count_npc_reappearances(self, campaign_id: str) -> int: ...

    def record_npc_reappearance(self, **values: Any) -> dict: ...

    def find_map_token_for_actor(
        self,
        map_id: str,
        actor_type: str,
        actor_id: str,
    ) -> dict | None: ...

    def get_map(
        self,
        map_id: str,
        allowed_visibility: tuple[str, ...] = ("player", "table", "kp"),
    ) -> dict: ...

    def place_map_token(self, map_id: str, label: str, location_name: str, **values: Any) -> dict: ...

    def move_map_token(self, token_id: str, to_location_name: str, **values: Any) -> dict: ...

    def get_world_expansion_materialization(self, proposal_id: str) -> dict | None: ...

    def find_world_expansion_materialization_by_key(
        self,
        campaign_id: str,
        idempotency_key: str,
    ) -> dict | None: ...

    def create_world_expansion_materialization(self, **values: Any) -> dict: ...


__all__ = ["WorldExpansionMaterializationStore"]
