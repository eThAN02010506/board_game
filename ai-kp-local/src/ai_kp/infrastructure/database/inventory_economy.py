"""Currency, transfer-offer, and recipe persistence for campaign inventory."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class InventoryEconomyRepository(SQLiteRepository):
    def get_currency_account(
        self, campaign_id: str, account_kind: str, account_id: str, currency_code: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM campaign_currency_accounts
            WHERE campaign_id = ? AND account_kind = ? AND account_id = ?
              AND currency_code = ?
            """,
            (campaign_id, account_kind, account_id, currency_code),
        ).fetchone()
        return row_to_dict(row) if row is not None else None

    def set_currency_balance(
        self,
        *,
        campaign_id: str,
        account_kind: str,
        account_id: str,
        currency_code: str,
        balance_minor: int,
        expected_version: int | None,
    ) -> dict[str, Any]:
        if expected_version is None:
            self.connection.execute(
                """
                INSERT INTO campaign_currency_accounts
                  (campaign_id, account_kind, account_id, currency_code, balance_minor)
                VALUES (?, ?, ?, ?, ?)
                """,
                (campaign_id, account_kind, account_id, currency_code, balance_minor),
            )
        else:
            updated = self.connection.execute(
                """
                UPDATE campaign_currency_accounts
                SET balance_minor = ?, version = version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE campaign_id = ? AND account_kind = ? AND account_id = ?
                  AND currency_code = ? AND version = ?
                """,
                (
                    balance_minor,
                    campaign_id,
                    account_kind,
                    account_id,
                    currency_code,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("Currency account changed; refresh before retrying")
        return self.get_currency_account(
            campaign_id, account_kind, account_id, currency_code
        ) or {}

    def list_currency_accounts(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM campaign_currency_accounts
            WHERE campaign_id = ? ORDER BY account_kind, account_id, currency_code
            """,
            (campaign_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def create_inventory_transfer_offer(self, **values: Any) -> dict[str, Any]:
        offer_id = new_id("itemoffer")
        self.connection.execute(
            """
            INSERT INTO inventory_transfer_offers
              (id, campaign_id, item_id, quantity, from_investigator_id,
               to_investigator_id, offered_by_member_id, item_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                offer_id,
                values["campaign_id"],
                values["item_id"],
                values["quantity"],
                values["from_investigator_id"],
                values["to_investigator_id"],
                values["offered_by_member_id"],
                values["item_version"],
            ),
        )
        return self.get_inventory_transfer_offer(offer_id)

    def get_inventory_transfer_offer(self, offer_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM inventory_transfer_offers WHERE id = ?", (offer_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Inventory transfer offer not found: {offer_id}")
        return row_to_dict(row)

    def list_inventory_transfer_offers(
        self, campaign_id: str, *, status: str = "pending"
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM inventory_transfer_offers
            WHERE campaign_id = ? AND status = ?
            ORDER BY created_at, id
            """,
            (campaign_id, status),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def transition_inventory_transfer_offer(
        self,
        offer_id: str,
        *,
        expected_version: int,
        from_status: str,
        to_status: str,
    ) -> dict[str, Any]:
        updated = self.connection.execute(
            """
            UPDATE inventory_transfer_offers
            SET status = ?, version = version + 1,
                updated_at = CURRENT_TIMESTAMP, resolved_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ? AND status = ?
            """,
            (to_status, offer_id, expected_version, from_status),
        )
        if updated.rowcount != 1:
            raise ValueError("Inventory transfer offer changed; refresh before retrying")
        return self.get_inventory_transfer_offer(offer_id)

    def create_inventory_recipe(self, **values: Any) -> dict[str, Any]:
        recipe_id = new_id("recipe")
        self.connection.execute(
            """
            INSERT INTO inventory_recipes
              (id, campaign_id, public_name, inputs_json, output_template_json,
               currency_cost_minor, currency_code, visibility, source_refs_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recipe_id,
                values["campaign_id"],
                values["public_name"],
                json.dumps(values.get("inputs", []), ensure_ascii=False, sort_keys=True),
                json.dumps(
                    values["output_template"], ensure_ascii=False, sort_keys=True
                ),
                int(values.get("currency_cost_minor", 0)),
                values.get("currency_code", ""),
                values.get("visibility", "table"),
                json.dumps(values.get("source_refs", []), ensure_ascii=False, sort_keys=True),
            ),
        )
        return self.get_inventory_recipe(recipe_id)

    def get_inventory_recipe(self, recipe_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM inventory_recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Inventory recipe not found: {recipe_id}")
        return self._decode_recipe(row)

    def list_inventory_recipes(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM inventory_recipes
            WHERE campaign_id = ? AND active = 1 ORDER BY public_name, id
            """,
            (campaign_id,),
        ).fetchall()
        return [self._decode_recipe(row) for row in rows]

    @staticmethod
    def _decode_recipe(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        result["active"] = bool(result["active"])
        result["inputs"] = decode_json_field(result.pop("inputs_json"), [])
        result["output_template"] = decode_json_field(
            result.pop("output_template_json"), {}
        )
        result["source_refs"] = decode_json_field(result.pop("source_refs_json"), [])
        return result


__all__ = ["InventoryEconomyRepository"]
