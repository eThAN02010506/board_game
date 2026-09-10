"""Low-level inventory stack and currency operations used inside write transactions."""

from __future__ import annotations

from typing import Any

from ai_kp.application.errors import ConflictError, InvalidInputError


class InventoryTransactionPrimitives:
    def __init__(self, repo: Any):
        self.repo = repo

    def move_quantity(
        self,
        item: dict[str, Any],
        *,
        quantity: int,
        holder_kind: str,
        holder_id: str,
    ) -> dict[str, Any]:
        available = int(item["quantity"])
        if quantity <= 0 or quantity > available:
            raise InvalidInputError("Transfer quantity exceeds the available stack")
        if item["state"] != "available":
            raise ConflictError("Only an available item can be transferred")
        if bool(item["is_unique"]) and quantity != available:
            raise ConflictError("A unique item cannot be split")
        if item.get("equipped_slot") and quantity != available:
            raise ConflictError("Unequip an item before splitting its stack")
        if quantity == available:
            return self.repo.transition_inventory_item(
                item["id"],
                expected_version=int(item["version"]),
                quantity=available,
                holder_kind=holder_kind,
                holder_id=holder_id,
                state="available",
                equipped_slot=None,
                known_member_ids=list(item.get("known_member_ids") or []),
            )
        self.repo.transition_inventory_item(
            item["id"],
            expected_version=int(item["version"]),
            quantity=available - quantity,
            holder_kind=str(item["holder_kind"]),
            holder_id=str(item["holder_id"]),
            state="available",
            equipped_slot=item.get("equipped_slot"),
            known_member_ids=list(item.get("known_member_ids") or []),
        )
        return self.repo.create_inventory_item(
            campaign_id=item["campaign_id"],
            item_type=item["item_type"],
            public_name=item["public_name"],
            public_description=item["public_description"],
            publicly_listed=bool(item.get("publicly_listed")),
            quantity=quantity,
            is_unique=False,
            holder_kind=holder_kind,
            holder_id=holder_id,
            weight_units=int(item["weight_units"]),
            unit_value_minor=int(item["unit_value_minor"]),
            currency_code=item["currency_code"],
            use_effect=dict(item.get("use_effect") or {}),
            hidden_properties=dict(item.get("hidden_properties") or {}),
            known_member_ids=list(item.get("known_member_ids") or []),
            source_refs=[
                *list(item.get("source_refs") or []),
                {"kind": "split_from", "id": item["id"]},
            ],
        )

    def consume_stack(self, item: dict[str, Any], quantity: int) -> dict[str, Any]:
        available = int(item["quantity"])
        if item["state"] != "available" or quantity <= 0 or quantity > available:
            raise ConflictError("Crafting input is no longer available")
        remaining = available - quantity
        return self.repo.transition_inventory_item(
            item["id"],
            expected_version=int(item["version"]),
            quantity=remaining,
            holder_kind=item["holder_kind"],
            holder_id=item["holder_id"],
            state="available" if remaining else "consumed",
            equipped_slot=item.get("equipped_slot") if remaining else None,
            known_member_ids=list(item.get("known_member_ids") or []),
        )

    def exchange_currency(
        self,
        *,
        campaign_id: str,
        payer: tuple[str, str],
        payee: tuple[str, str],
        currency_code: str,
        amount_minor: int,
        expected_payer_version: int | None,
        expected_payee_version: int | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if amount_minor <= 0 or not currency_code:
            raise InvalidInputError("Trade requires a positive authoritative price")
        payer_account = self.repo.get_currency_account(
            campaign_id, payer[0], payer[1], currency_code
        )
        if payer_account is None or int(payer_account["balance_minor"]) < amount_minor:
            raise ConflictError("The paying account has insufficient funds")
        if expected_payer_version != int(payer_account["version"]):
            raise ConflictError("The paying account changed; refresh before retrying")
        payee_account = self.repo.get_currency_account(
            campaign_id, payee[0], payee[1], currency_code
        )
        actual_payee_version = (
            int(payee_account["version"]) if payee_account is not None else None
        )
        if expected_payee_version != actual_payee_version:
            raise ConflictError("The receiving account changed; refresh before retrying")
        payer_account = self.repo.set_currency_balance(
            campaign_id=campaign_id,
            account_kind=payer[0],
            account_id=payer[1],
            currency_code=currency_code,
            balance_minor=int(payer_account["balance_minor"]) - amount_minor,
            expected_version=int(payer_account["version"]),
        )
        payee_account = self.repo.set_currency_balance(
            campaign_id=campaign_id,
            account_kind=payee[0],
            account_id=payee[1],
            currency_code=currency_code,
            balance_minor=int((payee_account or {}).get("balance_minor") or 0)
            + amount_minor,
            expected_version=actual_payee_version,
        )
        return payer_account, payee_account


__all__ = ["InventoryTransactionPrimitives"]
