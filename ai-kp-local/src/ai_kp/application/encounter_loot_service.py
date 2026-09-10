"""Deterministically expose incapacitated NPC inventory as encounter loot."""

from __future__ import annotations

from typing import Any


class EncounterLootService:
    """Move existing authoritative items; never invent rewards from narration."""

    _INCAPACITATING_CONDITIONS = frozenset({"dead", "dying", "unconscious"})

    def __init__(self, repo: Any):
        self.repo = repo

    def materialize(
        self,
        encounter: dict[str, Any],
        *,
        actor_member_id: str,
    ) -> tuple[dict[str, Any], ...]:
        if encounter.get("status") != "completed":
            return ()
        campaign_id = str(encounter["campaign_id"])
        encounter_id = str(encounter["id"])
        moved: list[dict[str, Any]] = []
        for participant in encounter["state"]["participants"]:
            npc_id = str(participant.get("npc_id") or "").strip()
            if not npc_id or not self._incapacitated(participant):
                continue
            for item in self.repo.list_inventory_items(
                campaign_id,
                holder_kind="npc",
                holder_id=npc_id,
            ):
                command_id = f"encounter-loot:{encounter_id}:{item['id']}"
                prior = self.repo.find_inventory_ledger_event(campaign_id, command_id)
                if prior is not None:
                    continue
                transitioned = self.repo.transition_inventory_item(
                    str(item["id"]),
                    expected_version=int(item["version"]),
                    quantity=int(item["quantity"]),
                    holder_kind="loot",
                    holder_id=encounter_id,
                    state="available",
                    equipped_slot=None,
                    known_member_ids=list(item.get("known_member_ids") or []),
                )
                self.repo.append_inventory_ledger_event(
                    campaign_id=campaign_id,
                    command_id=command_id,
                    command_type="encounter_loot",
                    item_id=str(item["id"]),
                    actor_member_id=actor_member_id,
                    reason="Existing NPC inventory became loot after incapacitation.",
                    before={"item": item},
                    after={"result": transitioned},
                )
                moved.append(transitioned)
        public = [
            {"id": item["id"], "public_name": item["public_name"]}
            for item in moved
            if item.get("publicly_listed")
        ]
        if public:
            self.repo.append_realtime_event(
                session_id=str(encounter["session_id"]),
                campaign_id=campaign_id,
                audience="session",
                event_type="inventory.encounter_loot_materialized",
                resource_type="coc7_encounter",
                resource_id=encounter_id,
                payload={"items": public},
            )
        return tuple(moved)

    @classmethod
    def _incapacitated(cls, participant: dict[str, Any]) -> bool:
        return any(
            condition.get("active", True)
            and condition.get("type") in cls._INCAPACITATING_CONDITIONS
            for condition in participant.get("conditions") or []
        )


__all__ = ["EncounterLootService"]
