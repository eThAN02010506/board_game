from fastapi import APIRouter, Query

from ai_kp.planning.capabilities import list_capabilities
from ai_kp.rulesets import list_rulesets


router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"ok": True}


@router.get("/rulesets")
def installed_rulesets() -> list[dict]:
    """List executable rule engines; uploaded books alone never appear here."""

    return list_rulesets()


@router.get("/capabilities")
def capabilities(
    include_available: bool = Query(
        default=True,
        description="Include features that are already available.",
    ),
) -> list[dict]:
    """Expose the product roadmap without creating fake feature endpoints."""

    return list_capabilities(include_available=include_available)
