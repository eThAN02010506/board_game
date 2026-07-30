"""Persistence contract for relationship-authorized NPC reappearance."""

from typing import Protocol

from ai_kp.application.ports.travel_graph import TravelGraphStore


class NpcReappearanceStore(TravelGraphStore, Protocol):
    def list_npc_reappearance_candidate_rows(
        self,
        campaign_id: str,
    ) -> list[dict]: ...

    def get_campaign(self, campaign_id: str) -> dict: ...

    def get_npc_availability_profile(self, npc_id: str) -> dict | None: ...

    def get_campaign_npc_reappearance_policy(self, campaign_id: str) -> dict: ...

    def count_npc_reappearances(self, campaign_id: str) -> int: ...

    def npc_is_linked_to_campaign(self, campaign_id: str, npc_id: str) -> bool: ...

    def save_npc_availability_profile(self, npc_id: str, **values) -> dict: ...

    def save_campaign_npc_reappearance_policy(
        self,
        campaign_id: str,
        **values,
    ) -> dict: ...

    def list_campaign_npcs_with_profiles(self, campaign_id: str) -> list[dict]: ...


__all__ = ["NpcReappearanceStore"]
