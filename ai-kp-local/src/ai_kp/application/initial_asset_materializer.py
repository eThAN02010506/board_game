"""Idempotently project approved character assets into the campaign ledger."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class InitialAssetMaterializer:
    """Translate only deterministic character-sheet fields; ambiguous text stays a record."""

    def __init__(self, repo: Any):
        self.repo = repo

    def materialize(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        revision_id: str,
        canonical_sheet: dict[str, Any],
        actor_member_id: str,
    ) -> None:
        assets = dict(canonical_sheet.get("assets") or {})
        for index, raw in enumerate(list(assets.get("items") or [])):
            item = self._item(raw)
            if item is None:
                continue
            command_id = self._command_id(revision_id, "item", index, item)
            if self.repo.find_inventory_ledger_event(campaign_id, command_id):
                continue
            created = self.repo.create_inventory_item(
                id=f"initial-{command_id}",
                campaign_id=campaign_id,
                item_type=item["item_type"],
                public_name=item["public_name"],
                public_description=item["public_description"],
                publicly_listed=False,
                quantity=item["quantity"],
                is_unique=item["is_unique"],
                holder_kind="investigator",
                holder_id=investigator_id,
                weight_units=item["weight_units"],
                unit_value_minor=item["unit_value_minor"],
                currency_code=item["currency_code"],
                use_effect=item["use_effect"],
                hidden_properties=item["hidden_properties"],
                source_refs=[{"kind": "investigator_revision", "id": revision_id}],
            )
            self._record(
                campaign_id,
                command_id,
                created,
                actor_member_id,
                revision_id,
            )
        for index, account in enumerate(self._accounts(assets)):
            command_id = self._command_id(revision_id, "currency", index, account)
            if self.repo.find_inventory_ledger_event(campaign_id, command_id):
                continue
            existing = self.repo.get_currency_account(
                campaign_id,
                "investigator",
                investigator_id,
                account["currency_code"],
            )
            updated = self.repo.set_currency_balance(
                campaign_id=campaign_id,
                account_kind="investigator",
                account_id=investigator_id,
                currency_code=account["currency_code"],
                balance_minor=int((existing or {}).get("balance_minor") or 0)
                + account["amount_minor"],
                expected_version=(int(existing["version"]) if existing else None),
            )
            self.repo.append_inventory_ledger_event(
                campaign_id=campaign_id,
                command_id=command_id,
                command_type="materialize_initial_currency",
                item_id=None,
                actor_member_id=actor_member_id,
                reason="KP-approved investigator revision",
                before={"account": existing or {}},
                after={"result": {"account": updated, "source_revision_id": revision_id}},
            )
        narrative = assets.get("currency")
        if isinstance(narrative, str) and narrative.strip():
            self._materialize_narrative_currency(
                campaign_id=campaign_id,
                investigator_id=investigator_id,
                revision_id=revision_id,
                actor_member_id=actor_member_id,
                text=narrative.strip(),
            )

    def _materialize_narrative_currency(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        revision_id: str,
        actor_member_id: str,
        text: str,
    ) -> None:
        record = {
            "item_type": "financial_record",
            "public_name": "角色卡资金记录",
            "public_description": text,
        }
        command_id = self._command_id(revision_id, "currency_record", 0, record)
        if self.repo.find_inventory_ledger_event(campaign_id, command_id):
            return
        created = self.repo.create_inventory_item(
            id=f"initial-{command_id}",
            campaign_id=campaign_id,
            item_type=record["item_type"],
            public_name=record["public_name"],
            public_description=record["public_description"],
            publicly_listed=False,
            quantity=1,
            is_unique=True,
            holder_kind="investigator",
            holder_id=investigator_id,
            source_refs=[{"kind": "investigator_revision", "id": revision_id}],
        )
        self._record(campaign_id, command_id, created, actor_member_id, revision_id)

    def _record(
        self,
        campaign_id: str,
        command_id: str,
        created: dict[str, Any],
        actor_member_id: str,
        revision_id: str,
    ) -> None:
        self.repo.append_inventory_ledger_event(
            campaign_id=campaign_id,
            command_id=command_id,
            command_type="materialize_initial_item",
            item_id=created["id"],
            actor_member_id=actor_member_id,
            reason="KP-approved investigator revision",
            before={},
            after={"result": created, "source_revision_id": revision_id},
        )

    @staticmethod
    def _item(raw: Any) -> dict[str, Any] | None:
        if isinstance(raw, str):
            raw = {"name": raw}
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("public_name") or raw.get("name") or "").strip()
        if not name:
            return None
        quantity = max(1, int(raw.get("quantity") or 1))
        unique = bool(raw.get("is_unique", False))
        use_effect = raw.get("use_effect")
        if not (
            isinstance(use_effect, dict)
            and use_effect.get("kind") == "ruleset_character_command"
            and str(use_effect.get("command_type") or "").strip()
            and isinstance(use_effect.get("payload"), dict)
        ):
            use_effect = {}
        return {
            "item_type": str(raw.get("item_type") or "equipment").strip(),
            "public_name": name,
            "public_description": str(
                raw.get("public_description") or raw.get("description") or ""
            ).strip(),
            "quantity": 1 if unique else quantity,
            "is_unique": unique,
            "weight_units": max(0, int(raw.get("weight_units") or 0)),
            "unit_value_minor": max(0, int(raw.get("unit_value_minor") or 0)),
            "currency_code": str(raw.get("currency_code") or "").strip(),
            "use_effect": dict(use_effect),
            "hidden_properties": dict(raw.get("hidden_properties") or {}),
        }

    @staticmethod
    def _accounts(assets: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = list(assets.get("currency_accounts") or [])
        for key in ("cash", "currency"):
            value = assets.get(key)
            if isinstance(value, dict):
                candidates.append(value)
        accounts = []
        for value in candidates:
            if not isinstance(value, dict):
                continue
            code = str(value.get("currency_code") or value.get("code") or "").strip()
            amount = value.get("amount_minor")
            if code and isinstance(amount, int) and amount >= 0:
                accounts.append({"currency_code": code, "amount_minor": amount})
        return accounts

    @staticmethod
    def _command_id(revision_id: str, kind: str, index: int, value: Any) -> str:
        digest = hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        return f"initial-assets:{revision_id}:{kind}:{index}:{digest}"


__all__ = ["InitialAssetMaterializer"]
