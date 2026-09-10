"""Campaign ownership, holder, and authoring validation for inventory workflows."""

from __future__ import annotations

from typing import Any

from ai_kp.application.errors import InvalidInputError
from ai_kp.platform.sessions.models import AuthenticatedMember


class InventoryAuthority:
    def __init__(self, repo: Any):
        self.repo = repo

    def item_visible(
        self, item: dict[str, Any], identity: AuthenticatedMember
    ) -> bool:
        if identity.role == "kp":
            return True
        if item["holder_kind"] in {"party", "location", "loot"}:
            return True
        if item["holder_kind"] == "investigator":
            return bool(item.get("equipped_slot")) or self.owns_investigator(
                identity, item["holder_id"]
            )
        return item["holder_kind"] == "npc" and bool(item.get("publicly_listed"))

    def require_item_control(
        self, identity: AuthenticatedMember, item: dict[str, Any]
    ) -> None:
        if identity.role == "kp":
            return
        if item["holder_kind"] != "investigator":
            raise PermissionError("This item is not controlled by your investigator")
        self.require_owned_investigator(identity, item["holder_id"])

    def identity_investigator_id(self, identity: AuthenticatedMember) -> str:
        matches = [
            row["investigator_id"]
            for row in self.repo.list_campaign_investigators(identity.campaign_id)
            if row.get("legacy_pc_id") == identity.pc_id
            and row.get("status") == "approved"
        ]
        if len(matches) != 1:
            raise PermissionError("The player does not control one approved investigator")
        return str(matches[0])

    def owns_investigator(
        self, identity: AuthenticatedMember, investigator_id: str
    ) -> bool:
        if identity.role != "player" or not identity.pc_id:
            return False
        record = self.repo.get_campaign_investigator(
            identity.campaign_id, investigator_id
        )
        return (
            record.get("legacy_pc_id") == identity.pc_id
            and record.get("status") == "approved"
        )

    def require_owned_investigator(
        self, identity: AuthenticatedMember, investigator_id: str
    ) -> None:
        if not self.owns_investigator(identity, investigator_id):
            raise PermissionError("The player does not control this investigator")

    @staticmethod
    def require_campaign(identity: AuthenticatedMember, campaign_id: str) -> None:
        if identity.campaign_id != campaign_id or identity.role not in {
            "kp",
            "player",
            "observer",
        }:
            raise PermissionError("Campaign inventory access denied")

    @staticmethod
    def require_kp(identity: AuthenticatedMember, campaign_id: str) -> None:
        if identity.campaign_id != campaign_id or identity.role != "kp":
            raise PermissionError("Only this campaign's KP may perform this command")

    @staticmethod
    def validate_create(command: Any) -> None:
        if not command.command_id.strip() or not command.item_type.strip():
            raise InvalidInputError("Inventory command and item type are required")
        if not command.public_name.strip() or not command.holder_id.strip():
            raise InvalidInputError("Inventory item name and holder are required")
        if command.quantity <= 0 or (command.is_unique and command.quantity != 1):
            raise InvalidInputError("Unique items require quantity one")
        if command.weight_units < 0 or command.unit_value_minor < 0:
            raise InvalidInputError("Inventory weight and value cannot be negative")
        InventoryAuthority._validate_use_effect(command.use_effect or {}, "Inventory")
        if not command.source_refs and not command.reason.strip():
            raise InvalidInputError(
                "Inventory creation requires source references or an audited KP reason"
            )

    @staticmethod
    def validate_recipe(command: Any) -> None:
        if not command.command_id.strip() or not command.public_name.strip():
            raise InvalidInputError("Recipe command and public name are required")
        if not command.inputs:
            raise InvalidInputError("A recipe requires at least one input")
        input_types: set[str] = set()
        for requirement in command.inputs:
            item_type = str(requirement.get("item_type") or "").strip()
            quantity = int(requirement.get("quantity") or 0)
            if not item_type or quantity <= 0:
                raise InvalidInputError("Recipe inputs require item_type and quantity")
            if item_type in input_types:
                raise InvalidInputError("Recipe input item types must be unique")
            input_types.add(item_type)
        output = command.output_template
        if not str(output.get("item_type") or "").strip() or not str(
            output.get("public_name") or ""
        ).strip():
            raise InvalidInputError("Recipe output requires item_type and public_name")
        quantity = int(output.get("quantity") or 1)
        if quantity <= 0 or (bool(output.get("is_unique")) and quantity != 1):
            raise InvalidInputError("Recipe output has an invalid quantity")
        if any(int(output.get(field) or 0) < 0 for field in ("weight_units", "unit_value_minor")):
            raise InvalidInputError("Recipe output weight and value cannot be negative")
        InventoryAuthority._validate_use_effect(output.get("use_effect") or {}, "Recipe")
        if command.currency_cost_minor < 0:
            raise InvalidInputError("Recipe currency cost cannot be negative")
        if command.currency_cost_minor and not command.currency_code.strip():
            raise InvalidInputError("Priced recipes require a currency code")
        if not command.source_refs and not command.reason.strip():
            raise InvalidInputError(
                "Recipe creation requires source references or an audited KP reason"
            )

    def validate_holder(
        self, campaign_id: str, holder_kind: str, holder_id: str
    ) -> None:
        if holder_kind == "investigator":
            record = self.repo.get_campaign_investigator(campaign_id, holder_id)
            if record.get("status") != "approved":
                raise InvalidInputError("Inventory holder must be an approved investigator")
        elif holder_kind == "npc":
            try:
                self.repo.get_campaign_npc(campaign_id, holder_id)
            except KeyError as exc:
                raise InvalidInputError(
                    "Inventory NPC holder must already belong to this campaign"
                ) from exc
        elif holder_kind == "location":
            run = self.repo.get_active_campaign_module_run(campaign_id)
            if run is None:
                raise InvalidInputError("Location-held inventory requires an active module")
            entity = self.repo.get_module_entity(holder_id)
            if entity["module_id"] != run["module_id"]:
                raise InvalidInputError("Inventory location belongs to another module")
        elif holder_kind == "party" and holder_id != campaign_id:
            raise InvalidInputError("Party inventory must use the campaign id as holder")

    @staticmethod
    def _validate_use_effect(effect: Any, label: str) -> None:
        if effect and (
            not isinstance(effect, dict)
            or effect.get("kind") != "ruleset_character_command"
            or not str(effect.get("command_type") or "").strip()
            or not isinstance(effect.get("payload"), dict)
        ):
            raise InvalidInputError(
                f"{label} use_effect must be a complete ruleset_character_command"
            )


__all__ = ["InventoryAuthority"]
