"""Recipe authoring and atomic crafting workflow."""

from __future__ import annotations

from typing import Any

from ai_kp.application.errors import ConflictError
from ai_kp.application.inventory_authority import InventoryAuthority
from ai_kp.application.inventory_projection import InventoryProjection
from ai_kp.application.inventory_transactions import InventoryTransactionPrimitives
from ai_kp.application.session_continuity_service import SessionContinuityService
from ai_kp.platform.sessions.models import AuthenticatedMember


class InventoryCraftingService:
    def __init__(
        self,
        repo: Any,
        authority: InventoryAuthority,
        transactions: InventoryTransactionPrimitives,
    ):
        self.repo = repo
        self.authority = authority
        self.transactions = transactions

    def create_recipe(
        self, campaign_id: str, identity: AuthenticatedMember, command: Any
    ) -> dict[str, Any]:
        self.authority.require_kp(identity, campaign_id)
        self.authority.validate_recipe(command)
        self.repo.begin_immediate()
        replay = self._replay(campaign_id, command.command_id, identity)
        if replay is not None:
            return replay
        recipe = self.repo.create_inventory_recipe(
            campaign_id=campaign_id,
            public_name=command.public_name.strip(),
            inputs=list(command.inputs),
            output_template=command.output_template,
            currency_cost_minor=command.currency_cost_minor,
            currency_code=command.currency_code,
            visibility=command.visibility,
            source_refs=list(command.source_refs),
        )
        event = self._record(
            campaign_id=campaign_id,
            command_id=command.command_id,
            command_type="create_recipe",
            item_id=None,
            identity=identity,
            reason=command.reason,
            before={},
            result={"recipe": recipe},
        )
        return {
            "recipe": recipe,
            "event": InventoryProjection.event(event, identity),
            "idempotent_replay": False,
        }

    def craft(
        self, campaign_id: str, identity: AuthenticatedMember, command: Any
    ) -> dict[str, Any]:
        self.authority.require_campaign(identity, campaign_id)
        SessionContinuityService(self.repo).require_actions_allowed(identity)
        if identity.role != "kp":
            self.authority.require_owned_investigator(
                identity, command.investigator_id
            )
        self.repo.begin_immediate()
        replay = self._replay(campaign_id, command.command_id, identity)
        if replay is not None:
            return replay
        recipe = self.repo.get_inventory_recipe(command.recipe_id)
        if (
            recipe["campaign_id"] != campaign_id
            or not recipe["active"]
            or int(recipe["version"]) != command.expected_recipe_version
        ):
            raise ConflictError("Crafting recipe changed; refresh before retrying")
        inventory = self.repo.list_inventory_items(
            campaign_id,
            holder_kind="investigator",
            holder_id=command.investigator_id,
        )
        consumed: list[dict[str, Any]] = []
        for requirement in recipe["inputs"]:
            item_type = str(requirement.get("item_type") or "")
            remaining = int(requirement.get("quantity") or 0)
            if not item_type or remaining <= 0:
                raise ConflictError("Crafting recipe has an invalid input")
            for item in [row for row in inventory if row["item_type"] == item_type]:
                if remaining <= 0:
                    break
                amount = min(remaining, int(item["quantity"]))
                consumed.append(self.transactions.consume_stack(item, amount))
                remaining -= amount
            if remaining:
                raise ConflictError("The investigator lacks required crafting inputs")
        self._charge_recipe(campaign_id, command, recipe)
        output = dict(recipe["output_template"])
        output_item = self.repo.create_inventory_item(
            campaign_id=campaign_id,
            item_type=str(output["item_type"]),
            public_name=str(output["public_name"]),
            public_description=str(output.get("public_description") or ""),
            publicly_listed=bool(output.get("publicly_listed", False)),
            quantity=int(output.get("quantity") or 1),
            is_unique=bool(output.get("is_unique", False)),
            holder_kind="investigator",
            holder_id=command.investigator_id,
            weight_units=int(output.get("weight_units") or 0),
            unit_value_minor=int(output.get("unit_value_minor") or 0),
            currency_code=str(output.get("currency_code") or ""),
            use_effect=dict(output.get("use_effect") or {}),
            hidden_properties=dict(output.get("hidden_properties") or {}),
            source_refs=[{"kind": "recipe", "id": recipe["id"]}],
        )
        result = {"item": output_item, "consumed": consumed, "recipe_id": recipe["id"]}
        event = self._record(
            campaign_id=campaign_id,
            command_id=command.command_id,
            command_type="craft",
            item_id=output_item["id"],
            identity=identity,
            reason=command.reason,
            before={"inputs": inventory},
            result=result,
        )
        return {
            "item": InventoryProjection.item(output_item, identity),
            "event": InventoryProjection.event(event, identity),
            "idempotent_replay": False,
        }

    def _charge_recipe(self, campaign_id: str, command: Any, recipe: dict) -> None:
        cost = int(recipe["currency_cost_minor"])
        if not cost:
            return
        account = self.repo.get_currency_account(
            campaign_id, "investigator", command.investigator_id, recipe["currency_code"]
        )
        if account is None or int(account["balance_minor"]) < cost:
            raise ConflictError("The investigator lacks required crafting currency")
        if command.expected_currency_version != int(account["version"]):
            raise ConflictError("Crafting currency changed; refresh before retrying")
        self.repo.set_currency_balance(
            campaign_id=campaign_id,
            account_kind="investigator",
            account_id=command.investigator_id,
            currency_code=recipe["currency_code"],
            balance_minor=int(account["balance_minor"]) - cost,
            expected_version=int(account["version"]),
        )

    def _record(
        self, *, result: dict[str, Any], identity: AuthenticatedMember, **values: Any
    ) -> dict[str, Any]:
        return self.repo.append_inventory_ledger_event(
            after={"result": result}, actor_member_id=identity.member_id, **values
        )

    def _replay(
        self, campaign_id: str, command_id: str, identity: AuthenticatedMember
    ) -> dict[str, Any] | None:
        event = self.repo.find_inventory_ledger_event(campaign_id, command_id)
        if event is None:
            return None
        result = dict(event.get("after", {}).get("result") or {})
        item = result.get("item")
        if isinstance(item, dict):
            return {
                "item": InventoryProjection.item(item, identity),
                "event": InventoryProjection.event(event, identity),
                "idempotent_replay": True,
            }
        return {
            "result": result,
            "event": InventoryProjection.event(event, identity),
            "idempotent_replay": True,
        }


__all__ = ["InventoryCraftingService"]
