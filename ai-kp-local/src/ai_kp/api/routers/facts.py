"""HTTP boundary for the event-backed world-fact ledger."""

from fastapi import APIRouter, Depends

from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.fact_schemas import FactType, WorldFactCreate, WorldFactRetcon
from ai_kp.application.fact_service import (
    AssertWorldFactCommand,
    FactService,
    RetconWorldFactCommand,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter()


@router.post("/campaigns/{campaign_id}/facts")
def assert_world_fact(
    campaign_id: str,
    payload: WorldFactCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return FactService(repo).assert_fact(
        campaign_id,
        identity,
        AssertWorldFactCommand(
            fact_type=payload.fact_type,
            subject=payload.subject,
            predicate=payload.predicate,
            object_text=payload.object_text,
            pc_id=payload.pc_id,
            evidence_event_ids=tuple(payload.evidence_event_ids),
            source_reference=payload.source_reference,
            happened_at=payload.happened_at,
        ),
    )


@router.get("/campaigns/{campaign_id}/facts")
def list_world_facts(
    campaign_id: str,
    include_history: bool = False,
    fact_type: FactType | None = None,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    return FactService(repo).list_facts(
        campaign_id,
        identity,
        include_history=include_history,
        fact_type=fact_type,
    )


@router.get("/campaigns/{campaign_id}/facts/{fact_key}")
def get_world_fact(
    campaign_id: str,
    fact_key: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return FactService(repo).get_fact(campaign_id, fact_key, identity)


@router.post("/campaigns/{campaign_id}/facts/{fact_key}/retcon")
def retcon_world_fact(
    campaign_id: str,
    fact_key: str,
    payload: WorldFactRetcon,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return FactService(repo).retcon_fact(
        campaign_id,
        fact_key,
        identity,
        RetconWorldFactCommand(
            expected_head_event_id=payload.expected_head_event_id,
            reason=payload.reason,
            evidence_event_ids=tuple(payload.evidence_event_ids),
            source_reference=payload.source_reference,
            happened_at=payload.happened_at,
        ),
    )
