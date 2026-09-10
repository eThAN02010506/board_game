"""Role-safe projections for the authoritative inventory ledger."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ai_kp.platform.sessions.models import AuthenticatedMember


class InventoryProjection:
    @staticmethod
    def item(item: dict[str, Any], identity: AuthenticatedMember) -> dict[str, Any]:
        if identity.role == "kp":
            return deepcopy(item)
        projected = {
            key: deepcopy(item[key])
            for key in (
                "id",
                "campaign_id",
                "item_type",
                "public_name",
                "public_description",
                "publicly_listed",
                "quantity",
                "is_unique",
                "holder_kind",
                "holder_id",
                "state",
                "equipped_slot",
                "weight_units",
                "unit_value_minor",
                "currency_code",
                "version",
                "created_at",
                "updated_at",
            )
        }
        if identity.role == "player" and identity.member_id in set(
            item.get("known_member_ids") or []
        ):
            projected["revealed_properties"] = deepcopy(
                item.get("hidden_properties") or {}
            )
        return projected

    @staticmethod
    def balance(account: dict[str, Any], identity: AuthenticatedMember) -> dict[str, Any]:
        if identity.role == "observer":
            raise PermissionError("Observers cannot read campaign balances")
        return deepcopy(account)

    @staticmethod
    def event(event: dict[str, Any], identity: AuthenticatedMember) -> dict[str, Any]:
        if identity.role == "kp":
            return deepcopy(event)
        return {
            key: event[key]
            for key in (
                "id",
                "campaign_id",
                "command_type",
                "item_id",
                "actor_member_id",
                "created_at",
            )
        }


__all__ = ["InventoryProjection"]
