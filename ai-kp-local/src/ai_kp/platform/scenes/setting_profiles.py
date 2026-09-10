"""Versionable module setting profiles built from generic setting packs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_kp.platform.scenes.settlement_templates import (
    EntityArchetypeKind,
    RegionNodeRole,
    RegionSkeleton,
    SettingPack,
    SettlementKind,
    SettlementSkeleton,
    instantiate_region,
    instantiate_settlement,
)

MODULE_ENTITY_ARCHETYPE_KINDS: dict[str, frozenset[EntityArchetypeKind]] = {
    "npc": frozenset({"npc"}),
    "organization": frozenset({"organization"}),
    "item": frozenset({"item", "document", "vehicle"}),
    "clue": frozenset({"clue_carrier", "document", "item"}),
    "event": frozenset({"event"}),
}


class ConfiguredRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    region_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
    title: str = Field(min_length=1, max_length=200)
    pattern_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    role_counts: dict[RegionNodeRole, int] = Field(default_factory=dict, max_length=4)


class ConfiguredLocationBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    module_entity_id: str = Field(min_length=1, max_length=160)
    slot_id: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
    )


class ConfiguredEntityBinding(BaseModel):
    """Explicitly classifies one source entity without changing that entity."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    module_entity_id: str = Field(min_length=1, max_length=160)
    archetype_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    settlement_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$",
    )
    slot_id: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
    )

    @model_validator(mode="after")
    def colocated_slot_pair(self) -> ConfiguredEntityBinding:
        if (self.settlement_id is None) != (self.slot_id is None):
            raise ValueError("Entity binding settlement and scene slot must be supplied together")
        return self


class ConfiguredSettlement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    settlement_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
    title: str = Field(min_length=1, max_length=200)
    region_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
    node_id: str = Field(min_length=1, max_length=180)
    settlement_kind: SettlementKind
    include_typical: bool = True
    condition_tags: tuple[str, ...] = Field(default=(), max_length=16)
    location_bindings: tuple[ConfiguredLocationBinding, ...] = Field(
        default=(), max_length=256
    )

    @model_validator(mode="after")
    def unique_location_bindings(self) -> ConfiguredSettlement:
        ids = [item.module_entity_id for item in self.location_bindings]
        if len(ids) != len(set(ids)):
            raise ValueError("Settlement repeats a module location binding")
        return self


class SettingProfileDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["1"] = "1"
    regions: tuple[ConfiguredRegion, ...] = Field(min_length=1, max_length=16)
    settlements: tuple[ConfiguredSettlement, ...] = Field(min_length=1, max_length=192)
    entity_bindings: tuple[ConfiguredEntityBinding, ...] = Field(
        default=(), max_length=1024
    )

    @model_validator(mode="after")
    def validate_identity_uniqueness(self) -> SettingProfileDocument:
        region_ids = [item.region_id for item in self.regions]
        if len(region_ids) != len(set(region_ids)):
            raise ValueError("Setting profile contains duplicate region ids")
        settlement_ids = [item.settlement_id for item in self.settlements]
        if len(settlement_ids) != len(set(settlement_ids)):
            raise ValueError("Setting profile contains duplicate settlement ids")
        node_keys = [(item.region_id, item.node_id) for item in self.settlements]
        if len(node_keys) != len(set(node_keys)):
            raise ValueError("Setting profile maps a region node more than once")
        known_regions = set(region_ids)
        if any(item.region_id not in known_regions for item in self.settlements):
            raise ValueError("Setting profile settlement references an unknown region")
        entity_ids = [
            binding.module_entity_id
            for settlement in self.settlements
            for binding in settlement.location_bindings
        ]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("A module location may belong to only one settlement")
        bound_entity_ids = [item.module_entity_id for item in self.entity_bindings]
        if len(bound_entity_ids) != len(set(bound_entity_ids)):
            raise ValueError("A module entity may have only one archetype binding")
        known_settlements = set(settlement_ids)
        if any(
            item.settlement_id is not None
            and item.settlement_id not in known_settlements
            for item in self.entity_bindings
        ):
            raise ValueError("Entity binding references an unknown settlement")
        return self

    def settlement(self, settlement_id: str) -> ConfiguredSettlement:
        for item in self.settlements:
            if item.settlement_id == settlement_id:
                return item
        raise KeyError(f"Setting profile does not define settlement: {settlement_id}")


class ValidatedSettingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document: SettingProfileDocument
    region_skeletons: tuple[RegionSkeleton, ...]
    settlement_skeletons: tuple[SettlementSkeleton, ...]
    unassigned_location_entity_ids: tuple[str, ...]
    unassigned_entity_ids: tuple[str, ...]


def build_setting_profile_document(
    pack: SettingPack,
    regions: Iterable[ConfiguredRegion],
) -> SettingProfileDocument:
    """Build one complete settlement record per generated region node."""

    configured_regions = tuple(regions)
    settlements: list[ConfiguredSettlement] = []
    for region in configured_regions:
        skeleton = instantiate_region(
            pack,
            region_id=region.region_id,
            pattern_id=region.pattern_id,
            role_counts=region.role_counts,
        )
        for node in skeleton.nodes:
            settlements.append(
                ConfiguredSettlement(
                    settlement_id=node.node_id,
                    title=node.node_id,
                    region_id=region.region_id,
                    node_id=node.node_id,
                    settlement_kind=node.settlement_kind,
                )
            )
    return SettingProfileDocument(
        regions=configured_regions,
        settlements=tuple(settlements),
    )


def validate_setting_profile(
    pack: SettingPack,
    document: SettingProfileDocument,
    module_entities: Iterable[Mapping[str, object]],
) -> ValidatedSettingProfile:
    """Resolve graph and slot references against the exact pack and module graph."""

    entities = {str(item.get("id")): item for item in module_entities if item.get("id")}
    location_ids = {
        entity_id
        for entity_id, item in entities.items()
        if item.get("entity_type") == "location"
    }
    region_skeletons = tuple(
        instantiate_region(
            pack,
            region_id=region.region_id,
            pattern_id=region.pattern_id,
            role_counts=region.role_counts,
        )
        for region in document.regions
    )
    region_nodes = {
        region.region_id: {node.node_id: node for node in region.nodes}
        for region in region_skeletons
    }
    configured_node_keys = {
        (item.region_id, item.node_id) for item in document.settlements
    }
    generated_node_keys = {
        (region.region_id, node.node_id)
        for region in region_skeletons
        for node in region.nodes
    }
    if configured_node_keys != generated_node_keys:
        missing = sorted(generated_node_keys - configured_node_keys)
        unknown = sorted(configured_node_keys - generated_node_keys)
        raise ValueError(
            f"Setting profile settlement/node coverage mismatch; missing={missing}, unknown={unknown}"
        )

    settlement_skeletons: list[SettlementSkeleton] = []
    assigned: set[str] = set()
    settlement_slots: dict[str, set[str]] = {}
    for settlement in document.settlements:
        node = region_nodes[settlement.region_id][settlement.node_id]
        if settlement.settlement_kind != node.settlement_kind:
            raise ValueError(
                f"Settlement {settlement.settlement_id} kind does not match its region node"
            )
        skeleton = instantiate_settlement(
            pack,
            settlement_id=settlement.settlement_id,
            kind=settlement.settlement_kind,
            include_typical=settlement.include_typical,
            condition_tags=frozenset(settlement.condition_tags),
        )
        slots = {item.slot_id for item in skeleton.scene_slots}
        settlement_slots[settlement.settlement_id] = slots
        for binding in settlement.location_bindings:
            if binding.module_entity_id not in entities:
                raise ValueError(
                    f"Setting profile references unknown module entity: {binding.module_entity_id}"
                )
            if binding.module_entity_id not in location_ids:
                raise ValueError(
                    f"Setting profile entity is not a location: {binding.module_entity_id}"
                )
            if binding.slot_id is not None and binding.slot_id not in slots:
                raise ValueError(
                    f"Setting profile references unknown scene slot: {binding.slot_id}"
                )
            assigned.add(binding.module_entity_id)
        settlement_skeletons.append(skeleton)
    archetypes = {item.archetype_id: item for item in pack.entity_archetypes}
    assigned_entities: set[str] = set()
    for binding in document.entity_bindings:
        entity = entities.get(binding.module_entity_id)
        if entity is None:
            raise ValueError(
                f"Setting profile references unknown module entity: {binding.module_entity_id}"
            )
        entity_type = str(entity.get("entity_type") or "")
        allowed_kinds = MODULE_ENTITY_ARCHETYPE_KINDS.get(entity_type)
        if allowed_kinds is None:
            raise ValueError(
                f"Module entity type cannot use a setting archetype: {binding.module_entity_id}"
            )
        archetype = archetypes.get(binding.archetype_id)
        if archetype is None:
            raise ValueError(
                f"Setting profile references unknown entity archetype: {binding.archetype_id}"
            )
        if archetype.entity_kind not in allowed_kinds:
            raise ValueError(
                f"Entity archetype kind is incompatible with module entity: {binding.module_entity_id}"
            )
        if binding.settlement_id is not None and binding.slot_id is not None:
            if binding.slot_id not in settlement_slots[binding.settlement_id]:
                raise ValueError(
                    f"Entity binding references unknown scene slot: {binding.slot_id}"
                )
            if binding.slot_id not in archetype.applicable_scene_slots:
                raise ValueError(
                    f"Entity archetype does not apply to scene slot: {binding.archetype_id}"
                )
        assigned_entities.add(binding.module_entity_id)
    bindable_entity_ids = {
        entity_id
        for entity_id, item in entities.items()
        if str(item.get("entity_type") or "") in MODULE_ENTITY_ARCHETYPE_KINDS
    }
    return ValidatedSettingProfile(
        document=document,
        region_skeletons=region_skeletons,
        settlement_skeletons=tuple(settlement_skeletons),
        unassigned_location_entity_ids=tuple(sorted(location_ids - assigned)),
        unassigned_entity_ids=tuple(sorted(bindable_entity_ids - assigned_entities)),
    )


__all__ = [
    "ConfiguredEntityBinding",
    "ConfiguredLocationBinding",
    "ConfiguredRegion",
    "ConfiguredSettlement",
    "SettingProfileDocument",
    "ValidatedSettingProfile",
    "build_setting_profile_document",
    "validate_setting_profile",
]
