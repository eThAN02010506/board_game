"""Authoritative inventory, ownership, and currency endpoints."""

from fastapi import APIRouter, Depends

from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import (
    CurrencyTransferInput,
    InventoryCraftInput,
    InventoryItemCommandInput,
    InventoryItemCreateInput,
    InventoryOfferCreateInput,
    InventoryOfferDecisionInput,
    InventoryRecipeCreateInput,
    InventoryTradeInput,
)
from ai_kp.application.inventory_service import (
    CurrencyCommand,
    InventoryCraftCommand,
    InventoryItemCommand,
    InventoryItemCreate,
    InventoryOfferCreate,
    InventoryOfferDecision,
    InventoryRecipeCreate,
    InventoryService,
    InventoryTradeCommand,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter(tags=["inventory"])


@router.get("/campaigns/{campaign_id}/inventory")
def get_campaign_inventory(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return InventoryService(repo).list_state(campaign_id, identity)


@router.post("/campaigns/{campaign_id}/inventory/items")
def create_campaign_inventory_item(
    campaign_id: str,
    payload: InventoryItemCreateInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return InventoryService(repo).create_item(
        campaign_id,
        identity,
        InventoryItemCreate(
            **payload.model_dump(exclude={"source_refs"}),
            source_refs=tuple(payload.source_refs),
        ),
    )


@router.post("/inventory/items/{item_id}/commands")
def command_campaign_inventory_item(
    item_id: str,
    payload: InventoryItemCommandInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return InventoryService(repo).command_item(
        item_id, identity, InventoryItemCommand(**payload.model_dump())
    )


@router.post("/campaigns/{campaign_id}/currency/transfers")
def transfer_campaign_currency(
    campaign_id: str,
    payload: CurrencyTransferInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return InventoryService(repo).transfer_currency(
        campaign_id, identity, CurrencyCommand(**payload.model_dump())
    )


@router.post("/campaigns/{campaign_id}/inventory/offers")
def create_campaign_inventory_offer(
    campaign_id: str,
    payload: InventoryOfferCreateInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return InventoryService(repo).create_transfer_offer(
        campaign_id, identity, InventoryOfferCreate(**payload.model_dump())
    )


@router.post("/inventory/offers/{offer_id}/decisions")
def decide_campaign_inventory_offer(
    offer_id: str,
    payload: InventoryOfferDecisionInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return InventoryService(repo).decide_transfer_offer(
        offer_id, identity, InventoryOfferDecision(**payload.model_dump())
    )


@router.post("/campaigns/{campaign_id}/inventory/trades")
def trade_campaign_inventory_item(
    campaign_id: str,
    payload: InventoryTradeInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return InventoryService(repo).trade_item(
        campaign_id, identity, InventoryTradeCommand(**payload.model_dump())
    )


@router.post("/campaigns/{campaign_id}/inventory/recipes")
def create_campaign_inventory_recipe(
    campaign_id: str,
    payload: InventoryRecipeCreateInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return InventoryService(repo).create_recipe(
        campaign_id,
        identity,
        InventoryRecipeCreate(
            **payload.model_dump(exclude={"inputs", "source_refs"}),
            inputs=tuple(payload.inputs),
            source_refs=tuple(payload.source_refs),
        ),
    )


@router.post("/campaigns/{campaign_id}/inventory/crafts")
def craft_campaign_inventory_item(
    campaign_id: str,
    payload: InventoryCraftInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return InventoryService(repo).craft(
        campaign_id, identity, InventoryCraftCommand(**payload.model_dump())
    )


__all__ = ["router"]
