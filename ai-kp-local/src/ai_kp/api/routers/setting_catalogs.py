"""Read-only built-in setting and settlement template catalog."""

from fastapi import APIRouter, Query

from ai_kp.platform.scenes.builtin_setting_packs import (
    BUILTIN_SETTING_PACKS,
    get_setting_pack,
)
from ai_kp.platform.scenes.settlement_templates import (
    RegionNodeRole,
    SettlementKind,
    instantiate_region,
    instantiate_settlement,
)

router = APIRouter(prefix="/setting-catalogs", tags=["setting catalogs"])
CONDITION_TAG_QUERY = Query(default=[])


@router.get("")
def list_setting_catalogs() -> list[dict]:
    return [
        {
            "setting_pack_id": pack.setting_pack_id,
            "schema_version": pack.schema_version,
            "pack_version": pack.pack_version,
            "title": pack.title,
            "locale": pack.locale,
            "content_scope": pack.content_scope,
            "provenance": list(pack.provenance),
            "year_start": pack.year_start,
            "year_end": pack.year_end,
            "settlement_kinds": [item.kind for item in pack.settlements],
            "region_patterns": [
                {
                    "pattern_id": item.pattern_id,
                    "title": item.title,
                    "applicability_tags": list(item.applicability_tags),
                    "nodes": [
                        {
                            "node_role": node.node_role,
                            "settlement_kind": node.settlement_kind,
                            "minimum_count": node.minimum_count,
                            "maximum_count": node.maximum_count,
                        }
                        for node in item.nodes
                    ],
                    "routes": [route.model_dump(mode="json") for route in item.routes],
                }
                for item in pack.region_patterns
            ],
        }
        for pack in BUILTIN_SETTING_PACKS
    ]


@router.get("/{setting_pack_id}/settlements/{settlement_kind}")
def instantiate_setting_catalog(
    setting_pack_id: str,
    settlement_kind: SettlementKind,
    settlement_id: str = Query(min_length=1, max_length=120),
    include_typical: bool = True,
    condition_tag: list[str] = CONDITION_TAG_QUERY,
) -> dict:
    pack = get_setting_pack(setting_pack_id)
    return instantiate_settlement(
        pack,
        settlement_id=settlement_id,
        kind=settlement_kind,
        include_typical=include_typical,
        condition_tags=frozenset(condition_tag),
    ).model_dump(mode="json")


@router.get("/{setting_pack_id}/regions/{pattern_id}")
def instantiate_region_catalog(
    setting_pack_id: str,
    pattern_id: str,
    region_id: str = Query(min_length=1, max_length=120),
    hub_count: int | None = Query(default=None, ge=0, le=24),
    nearby_count: int | None = Query(default=None, ge=0, le=24),
    distant_count: int | None = Query(default=None, ge=0, le=24),
    rural_count: int | None = Query(default=None, ge=0, le=24),
) -> dict:
    requested: dict[RegionNodeRole, int] = {
        role: count
        for role, count in (
            ("hub", hub_count),
            ("nearby", nearby_count),
            ("distant", distant_count),
            ("rural", rural_count),
        )
        if count is not None
    }
    return instantiate_region(
        get_setting_pack(setting_pack_id),
        region_id=region_id,
        pattern_id=pattern_id,
        role_counts=requested,
    ).model_dump(mode="json")


__all__ = ["router"]
