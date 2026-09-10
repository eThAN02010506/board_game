"""Transactional inventory and economy commands with player-owned authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ai_kp.application.errors import ConflictError, InvalidInputError
from ai_kp.application.inventory_authority import InventoryAuthority
from ai_kp.application.inventory_crafting_service import InventoryCraftingService
from ai_kp.application.inventory_item_effect_service import InventoryItemEffectService
from ai_kp.application.inventory_projection import InventoryProjection
from ai_kp.application.inventory_transactions import InventoryTransactionPrimitives
from ai_kp.application.session_continuity_service import SessionContinuityService
from ai_kp.platform.sessions.models import AuthenticatedMember

HolderKind = Literal["investigator", "party", "npc", "location", "loot", "none"]


@dataclass(frozen=True)
class InventoryItemCreate:
    command_id: str
    item_type: str
    public_name: str
    public_description: str
    publicly_listed: bool
    quantity: int
    is_unique: bool
    holder_kind: HolderKind
    holder_id: str
    weight_units: int = 0
    unit_value_minor: int = 0
    currency_code: str = ""
    use_effect: dict[str, Any] | None = None
    hidden_properties: dict[str, Any] | None = None
    source_refs: tuple[dict[str, Any], ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class InventoryItemCommand:
    command_id: str
    command_type: Literal[
        "pickup", "drop", "equip", "unequip", "consume", "damage", "lose", "reveal"
    ]
    expected_version: int
    quantity: int = 1
    holder_kind: HolderKind | None = None
    holder_id: str | None = None
    equipped_slot: str | None = None
    reveal_member_id: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class CurrencyCommand:
    command_id: str
    from_kind: str | None
    from_id: str | None
    to_kind: str
    to_id: str
    currency_code: str
    amount_minor: int
    expected_from_version: int | None = None
    expected_to_version: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class InventoryOfferCreate:
    command_id: str
    item_id: str
    expected_item_version: int
    quantity: int
    to_investigator_id: str
    reason: str = ""


@dataclass(frozen=True)
class InventoryOfferDecision:
    command_id: str
    expected_version: int
    decision: Literal["accept", "decline", "cancel"]
    reason: str = ""


@dataclass(frozen=True)
class InventoryTradeCommand:
    command_id: str
    direction: Literal["purchase", "sell"]
    item_id: str
    expected_item_version: int
    quantity: int
    investigator_id: str
    counterparty_kind: Literal["npc", "vendor"]
    counterparty_id: str
    currency_code: str
    expected_investigator_balance_version: int | None
    expected_counterparty_balance_version: int | None
    reason: str = ""


@dataclass(frozen=True)
class InventoryRecipeCreate:
    command_id: str
    public_name: str
    inputs: tuple[dict[str, Any], ...]
    output_template: dict[str, Any]
    currency_cost_minor: int = 0
    currency_code: str = ""
    visibility: Literal["table", "kp"] = "table"
    source_refs: tuple[dict[str, Any], ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class InventoryCraftCommand:
    command_id: str
    recipe_id: str
    investigator_id: str
    expected_recipe_version: int
    expected_currency_version: int | None = None
    reason: str = ""


class InventoryService:
    def __init__(self, repo: Any):
        self.repo = repo
        self.authority = InventoryAuthority(repo)
        self.transactions = InventoryTransactionPrimitives(repo)
        self.crafting = InventoryCraftingService(repo, self.authority, self.transactions)

    def list_state(
        self, campaign_id: str, identity: AuthenticatedMember
    ) -> dict[str, Any]:
        self._require_campaign(identity, campaign_id)
        items = [
            item
            for item in self.repo.list_inventory_items(
                campaign_id, include_terminal=identity.role == "kp"
            )
            if self._item_visible(item, identity)
        ]
        balances = []
        if identity.role != "observer":
            balances = [
                account
                for account in self.repo.list_currency_accounts(campaign_id)
                if identity.role == "kp"
                or (
                    account["account_kind"] == "investigator"
                    and self._owns_investigator(identity, account["account_id"])
                )
                or account["account_kind"] == "party"
            ]
        offers = [
            offer
            for offer in self.repo.list_inventory_transfer_offers(campaign_id)
            if identity.role == "kp"
            or self._owns_investigator(identity, offer["from_investigator_id"])
            or self._owns_investigator(identity, offer["to_investigator_id"])
        ]
        recipes = [
            recipe
            for recipe in self.repo.list_inventory_recipes(campaign_id)
            if identity.role == "kp" or recipe["visibility"] == "table"
        ]
        self_investigator_id = (
            self._identity_investigator_id(identity) if identity.role == "player" else None
        )
        transfer_targets = []
        if identity.role in {"kp", "player"}:
            transfer_targets = [
                {
                    "investigator_id": row["investigator_id"],
                    "name": row["name"],
                }
                for row in self.repo.list_campaign_investigators(campaign_id)
                if row.get("status") == "approved"
                and row["investigator_id"] != self_investigator_id
            ]
        return {
            "items": [InventoryProjection.item(item, identity) for item in items],
            "balances": [
                InventoryProjection.balance(account, identity) for account in balances
            ],
            "transfer_offers": offers,
            "transfer_targets": transfer_targets,
            "self_investigator_id": self_investigator_id,
            "recipes": [self._project_recipe(recipe, identity) for recipe in recipes],
        }

    def create_item(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: InventoryItemCreate,
    ) -> dict[str, Any]:
        self._require_kp(identity, campaign_id)
        self._validate_create(command)
        self._validate_holder(campaign_id, command.holder_kind, command.holder_id)
        self.repo.begin_immediate()
        replay = self._replay(campaign_id, command.command_id, identity)
        if replay is not None:
            return replay
        item = self.repo.create_inventory_item(
            campaign_id=campaign_id,
            item_type=command.item_type.strip(),
            public_name=command.public_name.strip(),
            public_description=command.public_description.strip(),
            publicly_listed=command.publicly_listed,
            quantity=command.quantity,
            is_unique=command.is_unique,
            holder_kind=command.holder_kind,
            holder_id=command.holder_id.strip(),
            weight_units=command.weight_units,
            unit_value_minor=command.unit_value_minor,
            currency_code=command.currency_code.strip(),
            use_effect=command.use_effect or {},
            hidden_properties=command.hidden_properties or {},
            source_refs=list(command.source_refs),
        )
        event = self._record(
            campaign_id=campaign_id,
            command_id=command.command_id,
            command_type="create",
            item_id=item["id"],
            identity=identity,
            reason=command.reason,
            before={},
            result=item,
        )
        return self._result(item, event, identity)

    def command_item(
        self,
        item_id: str,
        identity: AuthenticatedMember,
        command: InventoryItemCommand,
    ) -> dict[str, Any]:
        item = self.repo.get_inventory_item(item_id)
        self._require_campaign(identity, item["campaign_id"])
        SessionContinuityService(self.repo).require_actions_allowed(identity)
        self.repo.begin_immediate()
        item = self.repo.get_inventory_item(item_id)
        replay = self._replay(item["campaign_id"], command.command_id, identity)
        if replay is not None:
            return replay
        if int(item["version"]) != command.expected_version:
            raise ConflictError("Inventory item changed; refresh before retrying")
        before = dict(item)
        updated = self._transition_item(item, identity, command)
        effect_result = None
        if command.command_type == "consume":
            effect_result = InventoryItemEffectService(self.repo).apply(
                item=item,
                identity=identity,
                command_id=command.command_id,
            )
        recorded_result = (
            {"item": updated, "effect": effect_result}
            if effect_result is not None
            else updated
        )
        event = self._record(
            campaign_id=item["campaign_id"],
            command_id=command.command_id,
            command_type=command.command_type,
            item_id=item_id,
            identity=identity,
            reason=command.reason,
            before=before,
            result=recorded_result,
        )
        result = self._result(updated, event, identity)
        if effect_result is not None:
            result["effect"] = effect_result
        return result

    def transfer_currency(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: CurrencyCommand,
    ) -> dict[str, Any]:
        self._require_campaign(identity, campaign_id)
        SessionContinuityService(self.repo).require_actions_allowed(identity)
        if command.amount_minor <= 0 or not command.currency_code.strip():
            raise InvalidInputError("Currency transfer requires a positive amount and code")
        if identity.role != "kp":
            if command.from_kind != "investigator" or not command.from_id:
                raise PermissionError("Players may only spend from their investigator account")
            self._require_owned_investigator(identity, command.from_id)
        self.repo.begin_immediate()
        replay = self._replay(campaign_id, command.command_id, identity)
        if replay is not None:
            return replay
        before: dict[str, Any] = {}
        if command.from_kind and command.from_id:
            source = self.repo.get_currency_account(
                campaign_id,
                command.from_kind,
                command.from_id,
                command.currency_code,
            )
            if source is None or int(source["balance_minor"]) < command.amount_minor:
                raise ConflictError("Currency account has insufficient funds")
            if command.expected_from_version != int(source["version"]):
                raise ConflictError("Currency account changed; refresh before retrying")
            before["from"] = source
            source = self.repo.set_currency_balance(
                campaign_id=campaign_id,
                account_kind=command.from_kind,
                account_id=command.from_id,
                currency_code=command.currency_code,
                balance_minor=int(source["balance_minor"]) - command.amount_minor,
                expected_version=int(source["version"]),
            )
        else:
            if identity.role != "kp":
                raise PermissionError("Only the KP may grant currency")
            source = None
        destination = self.repo.get_currency_account(
            campaign_id, command.to_kind, command.to_id, command.currency_code
        )
        if command.expected_to_version != (
            int(destination["version"]) if destination is not None else None
        ):
            raise ConflictError("Destination currency account changed; refresh before retrying")
        before["to"] = destination or {}
        destination = self.repo.set_currency_balance(
            campaign_id=campaign_id,
            account_kind=command.to_kind,
            account_id=command.to_id,
            currency_code=command.currency_code,
            balance_minor=int((destination or {}).get("balance_minor") or 0)
            + command.amount_minor,
            expected_version=(
                int(destination["version"]) if destination is not None else None
            ),
        )
        result = {"from": source, "to": destination, "amount_minor": command.amount_minor}
        event = self._record(
            campaign_id=campaign_id,
            command_id=command.command_id,
            command_type="currency_transfer" if source else "currency_grant",
            item_id=None,
            identity=identity,
            reason=command.reason,
            before=before,
            result=result,
        )
        return {
            "result": result,
            "event": InventoryProjection.event(event, identity),
            "idempotent_replay": False,
        }

    def create_transfer_offer(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: InventoryOfferCreate,
    ) -> dict[str, Any]:
        self._require_campaign(identity, campaign_id)
        SessionContinuityService(self.repo).require_actions_allowed(identity)
        item = self.repo.get_inventory_item(command.item_id)
        if item["campaign_id"] != campaign_id:
            raise KeyError(f"Inventory item not found: {command.item_id}")
        self._require_item_control(identity, item)
        if item["holder_kind"] != "investigator":
            raise ConflictError("Only an investigator-held item can be offered")
        if command.quantity <= 0 or command.quantity > int(item["quantity"]):
            raise InvalidInputError("Transfer quantity exceeds the available stack")
        target = self.repo.get_campaign_investigator(
            campaign_id, command.to_investigator_id
        )
        if target.get("status") != "approved":
            raise InvalidInputError("Transfer target must be an approved investigator")
        if command.to_investigator_id == item["holder_id"]:
            raise InvalidInputError("An item cannot be offered to its current holder")
        self.repo.begin_immediate()
        replay = self._replay(campaign_id, command.command_id, identity)
        if replay is not None:
            return replay
        current = self.repo.get_inventory_item(command.item_id)
        if int(current["version"]) != command.expected_item_version:
            raise ConflictError("Inventory item changed; refresh before offering it")
        offer = self.repo.create_inventory_transfer_offer(
            campaign_id=campaign_id,
            item_id=command.item_id,
            quantity=command.quantity,
            from_investigator_id=current["holder_id"],
            to_investigator_id=command.to_investigator_id,
            offered_by_member_id=identity.member_id,
            item_version=current["version"],
        )
        event = self._record(
            campaign_id=campaign_id,
            command_id=command.command_id,
            command_type="offer_transfer",
            item_id=command.item_id,
            identity=identity,
            reason=command.reason,
            before={"item": current},
            result={"offer": offer},
        )
        return {
            "offer": offer,
            "event": InventoryProjection.event(event, identity),
            "idempotent_replay": False,
        }

    def decide_transfer_offer(
        self,
        offer_id: str,
        identity: AuthenticatedMember,
        command: InventoryOfferDecision,
    ) -> dict[str, Any]:
        offer = self.repo.get_inventory_transfer_offer(offer_id)
        self._require_campaign(identity, offer["campaign_id"])
        SessionContinuityService(self.repo).require_actions_allowed(identity)
        if command.decision in {"accept", "decline"}:
            self._require_owned_investigator(identity, offer["to_investigator_id"])
        elif identity.role != "kp" and identity.member_id != offer["offered_by_member_id"]:
            raise PermissionError("Only the offering player may cancel this transfer")
        self.repo.begin_immediate()
        replay = self._replay(offer["campaign_id"], command.command_id, identity)
        if replay is not None:
            return replay
        offer = self.repo.get_inventory_transfer_offer(offer_id)
        if offer["status"] != "pending" or int(offer["version"]) != command.expected_version:
            raise ConflictError("Inventory transfer offer changed; refresh before responding")
        item = self.repo.get_inventory_item(offer["item_id"])
        before = {"offer": offer, "item": item}
        if command.decision == "accept":
            if (
                int(item["version"]) != int(offer["item_version"])
                or item["holder_kind"] != "investigator"
                or item["holder_id"] != offer["from_investigator_id"]
            ):
                self.repo.transition_inventory_transfer_offer(
                    offer_id,
                    expected_version=int(offer["version"]),
                    from_status="pending",
                    to_status="stale",
                )
                raise ConflictError("The offered item changed before acceptance")
            moved = self.transactions.move_quantity(
                item,
                quantity=int(offer["quantity"]),
                holder_kind="investigator",
                holder_id=str(offer["to_investigator_id"]),
            )
            to_status = "accepted"
        else:
            moved = item
            to_status = "declined" if command.decision == "decline" else "cancelled"
        resolved = self.repo.transition_inventory_transfer_offer(
            offer_id,
            expected_version=int(offer["version"]),
            from_status="pending",
            to_status=to_status,
        )
        result = {"offer": resolved, "item": moved}
        event = self._record(
            campaign_id=offer["campaign_id"],
            command_id=command.command_id,
            command_type=f"transfer_{command.decision}",
            item_id=item["id"],
            identity=identity,
            reason=command.reason,
            before=before,
            result=result,
        )
        return {
            "offer": resolved,
            "item": InventoryProjection.item(moved, identity),
            "event": InventoryProjection.event(event, identity),
            "idempotent_replay": False,
        }

    def trade_item(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: InventoryTradeCommand,
    ) -> dict[str, Any]:
        self._require_campaign(identity, campaign_id)
        SessionContinuityService(self.repo).require_actions_allowed(identity)
        if identity.role != "kp":
            self._require_owned_investigator(identity, command.investigator_id)
        if command.quantity <= 0:
            raise InvalidInputError("Trade quantity must be positive")
        self.repo.begin_immediate()
        replay = self._replay(campaign_id, command.command_id, identity)
        if replay is not None:
            return replay
        item = self.repo.get_inventory_item(command.item_id)
        if item["campaign_id"] != campaign_id or int(item["version"]) != command.expected_item_version:
            raise ConflictError("Trade item changed; refresh before retrying")
        if command.quantity > int(item["quantity"]):
            raise InvalidInputError("Trade quantity exceeds the available stack")
        price = int(item["unit_value_minor"]) * command.quantity
        if item["currency_code"] != command.currency_code or price <= 0:
            raise ConflictError("This item has no authoritative price in that currency")
        if command.direction == "purchase":
            if item["holder_kind"] != "npc" or item["holder_id"] != command.counterparty_id:
                raise ConflictError("The counterparty no longer holds this item")
            payer = ("investigator", command.investigator_id)
            payee = (command.counterparty_kind, command.counterparty_id)
            destination = ("investigator", command.investigator_id)
        else:
            if item["holder_kind"] != "investigator" or item["holder_id"] != command.investigator_id:
                raise ConflictError("The investigator no longer holds this item")
            payer = (command.counterparty_kind, command.counterparty_id)
            payee = ("investigator", command.investigator_id)
            destination = ("npc", command.counterparty_id)
        payer_account, payee_account = self.transactions.exchange_currency(
            campaign_id=campaign_id,
            payer=payer,
            payee=payee,
            currency_code=command.currency_code,
            amount_minor=price,
            expected_payer_version=(
                command.expected_investigator_balance_version
                if command.direction == "purchase"
                else command.expected_counterparty_balance_version
            ),
            expected_payee_version=(
                command.expected_counterparty_balance_version
                if command.direction == "purchase"
                else command.expected_investigator_balance_version
            ),
        )
        moved = self.transactions.move_quantity(
            item,
            quantity=command.quantity,
            holder_kind=destination[0],
            holder_id=destination[1],
        )
        result = {
            "item": moved,
            "payer": payer_account,
            "payee": payee_account,
            "price_minor": price,
        }
        event = self._record(
            campaign_id=campaign_id,
            command_id=command.command_id,
            command_type=command.direction,
            item_id=moved["id"],
            identity=identity,
            reason=command.reason,
            before={"item": item},
            result=result,
        )
        return {
            "item": InventoryProjection.item(moved, identity),
            "price_minor": price,
            "event": InventoryProjection.event(event, identity),
            "idempotent_replay": False,
        }

    def create_recipe(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: InventoryRecipeCreate,
    ) -> dict[str, Any]:
        return self.crafting.create_recipe(campaign_id, identity, command)

    def craft(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: InventoryCraftCommand,
    ) -> dict[str, Any]:
        return self.crafting.craft(campaign_id, identity, command)

    def _transition_item(
        self,
        item: dict[str, Any],
        identity: AuthenticatedMember,
        command: InventoryItemCommand,
    ) -> dict[str, Any]:
        quantity = int(item["quantity"])
        state = str(item["state"])
        holder_kind = str(item["holder_kind"])
        holder_id = str(item["holder_id"])
        equipped_slot = item.get("equipped_slot")
        known = list(item.get("known_member_ids") or [])
        if command.command_type == "pickup":
            if identity.role != "player" or not identity.pc_id:
                raise PermissionError("A player investigator must pick up this item")
            investigator_id = self._identity_investigator_id(identity)
            if holder_kind not in {"location", "loot", "party"} or state != "available":
                raise ConflictError("This item is not available to pick up")
            if holder_kind == "location":
                run = self.repo.get_active_campaign_module_run(item["campaign_id"])
                if run is None or run.get("current_location_entity_id") != holder_id:
                    raise ConflictError("The item is not at the party's current location")
            holder_kind, holder_id = "investigator", investigator_id
        elif command.command_type == "drop":
            self._require_item_control(identity, item)
            if command.holder_kind not in {"location", "loot", "party"} or not command.holder_id:
                raise InvalidInputError("Drop requires a visible destination holder")
            self._validate_holder(
                item["campaign_id"], command.holder_kind, command.holder_id
            )
            holder_kind, holder_id, equipped_slot = (
                command.holder_kind,
                command.holder_id,
                None,
            )
        elif command.command_type == "equip":
            self._require_item_control(identity, item)
            if not command.equipped_slot:
                raise InvalidInputError("Equip requires an explicit slot")
            equipped_slot = command.equipped_slot.strip()
        elif command.command_type == "unequip":
            self._require_item_control(identity, item)
            equipped_slot = None
        elif command.command_type == "consume":
            self._require_item_control(identity, item)
            if command.quantity <= 0 or command.quantity > quantity:
                raise InvalidInputError("Consume quantity exceeds the available stack")
            quantity -= command.quantity
            equipped_slot = None if quantity == 0 else equipped_slot
            state = "consumed" if quantity == 0 else "available"
        elif command.command_type in {"damage", "lose"}:
            self._require_kp(identity, item["campaign_id"])
            state = "broken" if command.command_type == "damage" else "lost"
            equipped_slot = None
        elif command.command_type == "reveal":
            self._require_kp(identity, item["campaign_id"])
            if not command.reveal_member_id:
                raise InvalidInputError("Reveal requires a target member")
            member = self.repo.get_session_member(command.reveal_member_id)
            if member["campaign_id"] != item["campaign_id"]:
                raise InvalidInputError("Reveal member belongs to another campaign")
            known.append(command.reveal_member_id)
        return self.repo.transition_inventory_item(
            item["id"],
            expected_version=int(item["version"]),
            quantity=quantity,
            holder_kind=holder_kind,
            holder_id=holder_id,
            state=state,
            equipped_slot=equipped_slot,
            known_member_ids=known,
        )

    def _record(
        self,
        *,
        result: dict[str, Any],
        identity: AuthenticatedMember,
        **values: Any,
    ) -> dict[str, Any]:
        return self.repo.append_inventory_ledger_event(
            after={"result": result},
            actor_member_id=identity.member_id,
            **values,
        )

    def _replay(
        self, campaign_id: str, command_id: str, identity: AuthenticatedMember
    ) -> dict[str, Any] | None:
        event = self.repo.find_inventory_ledger_event(campaign_id, command_id)
        if event is None:
            return None
        result = dict(event.get("after", {}).get("result") or {})
        if "id" in result and result.get("campaign_id") == campaign_id:
            return self._result(result, event, identity, replay=True)
        nested_item = result.get("item")
        if isinstance(nested_item, dict) and nested_item.get("campaign_id") == campaign_id:
            replay = self._result(nested_item, event, identity, replay=True)
            for key in ("effect", "price_minor"):
                if key in result:
                    replay[key] = result[key]
            if isinstance(result.get("offer"), dict):
                replay["offer"] = result["offer"]
            return replay
        return {
            "result": result,
            "event": InventoryProjection.event(event, identity),
            "idempotent_replay": True,
        }

    @staticmethod
    def _result(
        item: dict[str, Any],
        event: dict[str, Any],
        identity: AuthenticatedMember,
        *,
        replay: bool = False,
    ) -> dict[str, Any]:
        return {
            "item": InventoryProjection.item(item, identity),
            "event": InventoryProjection.event(event, identity),
            "idempotent_replay": replay,
        }

    def _item_visible(self, item: dict[str, Any], identity: AuthenticatedMember) -> bool:
        return self.authority.item_visible(item, identity)

    def _require_item_control(
        self, identity: AuthenticatedMember, item: dict[str, Any]
    ) -> None:
        self.authority.require_item_control(identity, item)

    def _identity_investigator_id(self, identity: AuthenticatedMember) -> str:
        return self.authority.identity_investigator_id(identity)

    def _owns_investigator(
        self, identity: AuthenticatedMember, investigator_id: str
    ) -> bool:
        return self.authority.owns_investigator(identity, investigator_id)

    def _require_owned_investigator(
        self, identity: AuthenticatedMember, investigator_id: str
    ) -> None:
        self.authority.require_owned_investigator(identity, investigator_id)

    @staticmethod
    def _require_campaign(identity: AuthenticatedMember, campaign_id: str) -> None:
        InventoryAuthority.require_campaign(identity, campaign_id)

    @staticmethod
    def _require_kp(identity: AuthenticatedMember, campaign_id: str) -> None:
        InventoryAuthority.require_kp(identity, campaign_id)

    @staticmethod
    def _validate_create(command: InventoryItemCreate) -> None:
        InventoryAuthority.validate_create(command)

    @staticmethod
    def _validate_recipe(command: InventoryRecipeCreate) -> None:
        InventoryAuthority.validate_recipe(command)

    def _validate_holder(
        self, campaign_id: str, holder_kind: str, holder_id: str
    ) -> None:
        self.authority.validate_holder(campaign_id, holder_kind, holder_id)

    @staticmethod
    def _project_recipe(
        recipe: dict[str, Any], identity: AuthenticatedMember
    ) -> dict[str, Any]:
        if identity.role == "kp":
            return recipe
        return {
            key: recipe[key]
            for key in (
                "id",
                "campaign_id",
                "public_name",
                "inputs",
                "currency_cost_minor",
                "currency_code",
                "version",
            )
        }


__all__ = [
    "CurrencyCommand",
    "InventoryCraftCommand",
    "InventoryItemCommand",
    "InventoryItemCreate",
    "InventoryOfferCreate",
    "InventoryOfferDecision",
    "InventoryRecipeCreate",
    "InventoryService",
    "InventoryTradeCommand",
]
