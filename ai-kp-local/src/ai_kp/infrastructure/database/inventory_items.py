"""Persistence primitives for authoritative campaign inventory items."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class InventoryItemRepository(SQLiteRepository):
    def create_inventory_item(self, **values: Any) -> dict[str, Any]:
        item_id = str(values.get("id") or new_id("item"))
        self.connection.execute(
            """
            INSERT INTO campaign_inventory_items
              (id, campaign_id, item_type, public_name, public_description,
               publicly_listed, quantity, is_unique, holder_kind, holder_id, state,
               equipped_slot, weight_units, unit_value_minor, currency_code,
               use_effect_json, hidden_properties_json, known_member_ids_json,
               source_refs_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                values["campaign_id"],
                values["item_type"],
                values["public_name"],
                values.get("public_description", ""),
                int(bool(values.get("publicly_listed", False))),
                int(values.get("quantity", 1)),
                int(bool(values.get("is_unique", False))),
                values["holder_kind"],
                values["holder_id"],
                values.get("state", "available"),
                values.get("equipped_slot"),
                int(values.get("weight_units", 0)),
                int(values.get("unit_value_minor", 0)),
                values.get("currency_code", ""),
                self._json(values.get("use_effect", {})),
                self._json(values.get("hidden_properties", {})),
                self._json(values.get("known_member_ids", [])),
                self._json(values.get("source_refs", [])),
            ),
        )
        return self.get_inventory_item(item_id)

    def get_inventory_item(self, item_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM campaign_inventory_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Inventory item not found: {item_id}")
        return self._decode_item(row)

    def list_inventory_items(
        self,
        campaign_id: str,
        *,
        holder_kind: str | None = None,
        holder_id: str | None = None,
        include_terminal: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = ["campaign_id = ?"]
        params: list[Any] = [campaign_id]
        if holder_kind is not None:
            clauses.append("holder_kind = ?")
            params.append(holder_kind)
        if holder_id is not None:
            clauses.append("holder_id = ?")
            params.append(holder_id)
        if not include_terminal:
            clauses.append("state = 'available'")
        rows = self.connection.execute(
            f"""
            SELECT * FROM campaign_inventory_items
            WHERE {" AND ".join(clauses)}
            ORDER BY public_name, created_at, id
            """,
            params,
        ).fetchall()
        return [self._decode_item(row) for row in rows]

    def transition_inventory_item(
        self,
        item_id: str,
        *,
        expected_version: int,
        quantity: int,
        holder_kind: str,
        holder_id: str,
        state: str,
        equipped_slot: str | None,
        known_member_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        known_sql = ""
        params: list[Any] = [
            quantity,
            holder_kind,
            holder_id,
            state,
            equipped_slot,
        ]
        if known_member_ids is not None:
            known_sql = ", known_member_ids_json = ?"
            params.append(self._json(sorted(set(known_member_ids))))
        params.extend((item_id, expected_version))
        updated = self.connection.execute(
            f"""
            UPDATE campaign_inventory_items
            SET quantity = ?, holder_kind = ?, holder_id = ?, state = ?,
                equipped_slot = ?{known_sql}, version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ?
            """,
            params,
        )
        if updated.rowcount != 1:
            raise ValueError("Inventory item changed; refresh before retrying")
        return self.get_inventory_item(item_id)

    def find_inventory_ledger_event(
        self, campaign_id: str, command_id: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM inventory_ledger_events
            WHERE campaign_id = ? AND command_id = ?
            """,
            (campaign_id, command_id),
        ).fetchone()
        return self._decode_event(row) if row is not None else None

    def append_inventory_ledger_event(
        self,
        *,
        campaign_id: str,
        command_id: str,
        command_type: str,
        item_id: str | None,
        actor_member_id: str | None,
        reason: str,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> dict[str, Any]:
        event_id = new_id("inventoryevent")
        self.connection.execute(
            """
            INSERT INTO inventory_ledger_events
              (id, campaign_id, command_id, command_type, item_id,
               actor_member_id, reason, before_json, after_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                campaign_id,
                command_id,
                command_type,
                item_id,
                actor_member_id,
                reason,
                self._json(before),
                self._json(after),
            ),
        )
        return self.find_inventory_ledger_event(campaign_id, command_id) or {}

    def list_inventory_ledger_events(
        self, campaign_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM inventory_ledger_events
            WHERE campaign_id = ? ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (campaign_id, limit),
        ).fetchall()
        return [self._decode_event(row) for row in rows]

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _decode_item(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        result["is_unique"] = bool(result["is_unique"])
        result["publicly_listed"] = bool(result["publicly_listed"])
        for output, raw, default in (
            ("use_effect", "use_effect_json", {}),
            ("hidden_properties", "hidden_properties_json", {}),
            ("known_member_ids", "known_member_ids_json", []),
            ("source_refs", "source_refs_json", []),
        ):
            result[output] = decode_json_field(result.pop(raw), default)
        return result

    @staticmethod
    def _decode_event(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        result["before"] = decode_json_field(result.pop("before_json"), {})
        result["after"] = decode_json_field(result.pop("after_json"), {})
        return result


__all__ = ["InventoryItemRepository"]
