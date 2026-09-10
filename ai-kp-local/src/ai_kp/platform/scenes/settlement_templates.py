"""Ruleset-neutral era and settlement scene skeletons.

Templates describe social functions, not canon facts.  A caller must still name,
place, approve, and materialize each selected scene for a concrete campaign.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SettlementKind = Literal["city", "town", "village", "rural"]
SceneFrequency = Literal["core", "typical", "conditional"]
AccessScope = Literal["local", "nearby"]
RegionNodeRole = Literal["hub", "nearby", "distant", "rural"]
EntityArchetypeKind = Literal[
    "npc",
    "organization",
    "item",
    "document",
    "vehicle",
    "event",
    "clue_carrier",
]


class EraConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    available_technologies: tuple[str, ...] = Field(default=(), max_length=32)
    unavailable_technologies: tuple[str, ...] = Field(default=(), max_length=32)
    common_transport: tuple[str, ...] = Field(default=(), max_length=16)
    common_communications: tuple[str, ...] = Field(default=(), max_length=16)
    record_media: tuple[str, ...] = Field(default=(), max_length=16)
    cautions: tuple[str, ...] = Field(default=(), max_length=24)


class SceneFunctionDetail(BaseModel):
    """Reusable non-canon structure attached to one social function slot."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    detail_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    interior_zones: tuple[str, ...] = Field(default=(), max_length=16)
    profession_ids: tuple[str, ...] = Field(default=(), max_length=16)
    service_capabilities: tuple[str, ...] = Field(default=(), max_length=16)
    record_sources: tuple[str, ...] = Field(default=(), max_length=16)
    access_patterns: tuple[str, ...] = Field(default=(), max_length=12)
    investigation_surfaces: tuple[str, ...] = Field(default=(), max_length=16)
    event_seed_kinds: tuple[str, ...] = Field(default=(), max_length=12)


class SettlementSceneSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    slot_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    function: str = Field(min_length=1, max_length=120)
    frequency: SceneFrequency
    access_scope: AccessScope = "local"
    building_variants: tuple[str, ...] = Field(min_length=1, max_length=8)
    play_affordances: tuple[str, ...] = Field(default=(), max_length=8)
    condition_tags: tuple[str, ...] = Field(default=(), max_length=8)


class SettlementTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    kind: SettlementKind
    slots: tuple[SettlementSceneSlot, ...] = Field(min_length=1, max_length=48)

    @model_validator(mode="after")
    def unique_slot_ids(self) -> SettlementTemplate:
        ids = [item.slot_id for item in self.slots]
        if len(ids) != len(set(ids)):
            raise ValueError("Settlement template contains duplicate slot ids")
        return self


class RegionNodeTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_role: RegionNodeRole
    settlement_kind: SettlementKind
    minimum_count: int = Field(ge=0, le=12)
    maximum_count: int = Field(ge=1, le=24)

    @model_validator(mode="after")
    def valid_range(self) -> RegionNodeTemplate:
        if self.minimum_count > self.maximum_count:
            raise ValueError("Region node minimum exceeds maximum")
        return self


class RegionRouteTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    from_role: RegionNodeRole
    to_role: RegionNodeRole
    travel_modes: tuple[str, ...] = Field(min_length=1, max_length=8)
    distance_band: Literal["local", "near", "regional", "remote"]
    bidirectional: bool = True


class RegionPattern(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    pattern_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    title: str = Field(min_length=1, max_length=160)
    applicability_tags: tuple[str, ...] = Field(default=(), max_length=16)
    nodes: tuple[RegionNodeTemplate, ...] = Field(min_length=1, max_length=16)
    routes: tuple[RegionRouteTemplate, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_roles(self) -> RegionPattern:
        roles = [item.node_role for item in self.nodes]
        known_roles = set(roles)
        if len(roles) != len(known_roles):
            raise ValueError("Region pattern contains duplicate node roles")
        if "hub" not in known_roles:
            raise ValueError("Region pattern requires one hub role")
        if any(
            route.from_role not in known_roles or route.to_role not in known_roles
            for route in self.routes
        ):
            raise ValueError("Region pattern route references a missing node role")
        reachable = {"hub"}
        changed = True
        while changed:
            changed = False
            for route in self.routes:
                if route.from_role in reachable and route.to_role not in reachable:
                    reachable.add(route.to_role)
                    changed = True
                if route.to_role in reachable and route.from_role not in reachable:
                    reachable.add(route.from_role)
                    changed = True
        required_roles = {
            item.node_role for item in self.nodes if item.minimum_count > 0
        }
        if not required_roles <= reachable:
            raise ValueError("Region pattern leaves required node roles disconnected")
        return self


class ProfessionTemplate(BaseModel):
    """A fillable social role; never a concrete NPC identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    profession_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    titles: tuple[str, ...] = Field(min_length=1, max_length=8)
    institution_functions: tuple[str, ...] = Field(default=(), max_length=12)
    authority: tuple[str, ...] = Field(default=(), max_length=12)
    knowledge_domains: tuple[str, ...] = Field(default=(), max_length=16)
    schedule_patterns: tuple[str, ...] = Field(default=(), max_length=8)
    relationship_hooks: tuple[str, ...] = Field(default=(), max_length=12)


class EntityRelationSlot(BaseModel):
    """A typed, still-unfilled relationship required or suggested by an archetype."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    relation_slot_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    predicate: str = Field(min_length=1, max_length=120)
    target_kinds: tuple[EntityArchetypeKind, ...] = Field(min_length=1, max_length=7)
    target_archetype_ids: tuple[str, ...] = Field(default=(), max_length=24)
    minimum_count: int = Field(default=0, ge=0, le=12)
    maximum_count: int = Field(default=1, ge=1, le=24)

    @model_validator(mode="after")
    def valid_cardinality(self) -> EntityRelationSlot:
        if self.minimum_count > self.maximum_count:
            raise ValueError("Entity relation minimum exceeds maximum")
        if len(self.target_kinds) != len(set(self.target_kinds)):
            raise ValueError("Entity relation contains duplicate target kinds")
        if len(self.target_archetype_ids) != len(set(self.target_archetype_ids)):
            raise ValueError("Entity relation contains duplicate target archetypes")
        return self


class EntityArchetype(BaseModel):
    """A reusable capability/relationship shape, never a concrete world entity."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    archetype_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    entity_kind: EntityArchetypeKind
    label_variants: tuple[str, ...] = Field(min_length=1, max_length=12)
    applicable_scene_slots: tuple[str, ...] = Field(min_length=1, max_length=48)
    capability_tags: tuple[str, ...] = Field(default=(), max_length=24)
    profession_ids: tuple[str, ...] = Field(default=(), max_length=16)
    state_dimensions: tuple[str, ...] = Field(default=(), max_length=16)
    relation_slots: tuple[EntityRelationSlot, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_shape(self) -> EntityArchetype:
        relation_ids = [item.relation_slot_id for item in self.relation_slots]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("Entity archetype contains duplicate relation slot ids")
        if self.profession_ids and self.entity_kind != "npc":
            raise ValueError("Only NPC archetypes may reference professions")
        for label, values in (
            ("label variants", self.label_variants),
            ("scene slots", self.applicable_scene_slots),
            ("capability tags", self.capability_tags),
            ("professions", self.profession_ids),
            ("state dimensions", self.state_dimensions),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"Entity archetype contains duplicate {label}")
        return self


class AgentWorkflowStage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    stage_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    objective: str = Field(min_length=1, max_length=240)
    allowed_inputs: tuple[str, ...] = Field(default=(), max_length=16)
    output_records: tuple[str, ...] = Field(default=(), max_length=16)
    gate: str = Field(min_length=1, max_length=500)


class SettingPack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    setting_pack_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    schema_version: Literal["1"] = "1"
    pack_version: str = Field(default="1.0.0", pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    title: str = Field(min_length=1, max_length=160)
    locale: str = Field(min_length=1, max_length=120)
    content_scope: str = Field(min_length=1, max_length=500)
    provenance: tuple[str, ...] = Field(min_length=1, max_length=16)
    year_start: int = Field(ge=1, le=9999)
    year_end: int = Field(ge=1, le=9999)
    era_constraints: EraConstraints = Field(default_factory=EraConstraints)
    region_patterns: tuple[RegionPattern, ...] = Field(min_length=1, max_length=12)
    settlements: tuple[SettlementTemplate, ...] = Field(min_length=1, max_length=8)
    function_details: tuple[SceneFunctionDetail, ...] = Field(default=(), max_length=64)
    professions: tuple[ProfessionTemplate, ...] = Field(default=(), max_length=96)
    entity_archetypes: tuple[EntityArchetype, ...] = Field(default=(), max_length=192)
    agent_workflow: tuple[AgentWorkflowStage, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def validate_pack(self) -> SettingPack:
        if self.year_start > self.year_end:
            raise ValueError("Setting pack year range is reversed")
        kinds = [item.kind for item in self.settlements]
        if len(kinds) != len(set(kinds)):
            raise ValueError("Setting pack contains duplicate settlement kinds")
        referenced = {
            node.settlement_kind for pattern in self.region_patterns for node in pattern.nodes
        }
        if not referenced <= set(kinds):
            raise ValueError("Region structure references a missing settlement template")
        detail_ids = [item.detail_id for item in self.function_details]
        if len(detail_ids) != len(set(detail_ids)):
            raise ValueError("Setting pack contains duplicate function detail ids")
        pattern_ids = [item.pattern_id for item in self.region_patterns]
        if len(pattern_ids) != len(set(pattern_ids)):
            raise ValueError("Setting pack contains duplicate region pattern ids")
        profession_ids = [item.profession_id for item in self.professions]
        if len(profession_ids) != len(set(profession_ids)):
            raise ValueError("Setting pack contains duplicate profession ids")
        unknown_professions = sorted(
            {
                profession_id
                for detail in self.function_details
                for profession_id in detail.profession_ids
                if profession_id not in set(profession_ids)
            }
        )
        if unknown_professions:
            raise ValueError(
                "Function details reference unknown professions: " + ", ".join(unknown_professions)
            )
        archetype_ids = [item.archetype_id for item in self.entity_archetypes]
        if len(archetype_ids) != len(set(archetype_ids)):
            raise ValueError("Setting pack contains duplicate entity archetype ids")
        stage_ids = [item.stage_id for item in self.agent_workflow]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("Setting pack contains duplicate Agent workflow stages")
        slot_ids = {
            slot.slot_id for settlement in self.settlements for slot in settlement.slots
        }
        if not slot_ids <= set(detail_ids):
            raise ValueError("Every settlement slot requires a function detail")
        unknown_archetype_slots = sorted(
            {
                slot_id
                for archetype in self.entity_archetypes
                for slot_id in archetype.applicable_scene_slots
                if slot_id not in slot_ids
            }
        )
        if unknown_archetype_slots:
            raise ValueError(
                "Entity archetypes reference unknown scene slots: "
                + ", ".join(unknown_archetype_slots)
            )
        known_archetypes = set(archetype_ids)
        unknown_archetype_professions = sorted(
            {
                profession_id
                for archetype in self.entity_archetypes
                for profession_id in archetype.profession_ids
                if profession_id not in set(profession_ids)
            }
        )
        if unknown_archetype_professions:
            raise ValueError(
                "Entity archetypes reference unknown professions: "
                + ", ".join(unknown_archetype_professions)
            )
        unknown_relation_targets = sorted(
            {
                target_id
                for archetype in self.entity_archetypes
                for relation in archetype.relation_slots
                for target_id in relation.target_archetype_ids
                if target_id not in known_archetypes
            }
        )
        if unknown_relation_targets:
            raise ValueError(
                "Entity relations reference unknown archetypes: "
                + ", ".join(unknown_relation_targets)
            )
        archetypes_by_id = {item.archetype_id: item for item in self.entity_archetypes}
        wrong_relation_target_kinds = sorted(
            {
                target_id
                for archetype in self.entity_archetypes
                for relation in archetype.relation_slots
                for target_id in relation.target_archetype_ids
                if target_id in archetypes_by_id
                and archetypes_by_id[target_id].entity_kind not in relation.target_kinds
            }
        )
        if wrong_relation_target_kinds:
            raise ValueError(
                "Entity relations target incompatible archetype kinds: "
                + ", ".join(wrong_relation_target_kinds)
            )
        return self

    def settlement(self, kind: SettlementKind) -> SettlementTemplate:
        for item in self.settlements:
            if item.kind == kind:
                return item
        raise KeyError(f"Setting pack does not define settlement kind: {kind}")

    def function_detail(self, slot_id: str) -> SceneFunctionDetail | None:
        return next(
            (item for item in self.function_details if item.detail_id == slot_id),
            None,
        )

    def region_pattern(self, pattern_id: str) -> RegionPattern:
        for item in self.region_patterns:
            if item.pattern_id == pattern_id:
                return item
        raise KeyError(f"Setting pack does not define region pattern: {pattern_id}")


class InstantiatedSceneSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_id: str
    slot_id: str
    function: str
    frequency: SceneFrequency
    access_scope: AccessScope
    building_candidates: tuple[str, ...]
    play_affordances: tuple[str, ...]
    interior_zones: tuple[str, ...]
    profession_candidates: tuple[ProfessionTemplate, ...]
    entity_archetype_candidates: tuple[EntityArchetype, ...]
    service_capabilities: tuple[str, ...]
    record_sources: tuple[str, ...]
    access_patterns: tuple[str, ...]
    investigation_surfaces: tuple[str, ...]
    event_seed_kinds: tuple[str, ...]
    selected: bool


class SettlementSkeleton(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    setting_pack_id: str
    settlement_id: str
    settlement_kind: SettlementKind
    scene_slots: tuple[InstantiatedSceneSlot, ...]
    assumptions: tuple[str, ...]
    era_constraints: EraConstraints
    agent_workflow: tuple[AgentWorkflowStage, ...]


class RegionNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    node_role: RegionNodeRole
    settlement_kind: SettlementKind
    fill_status: Literal["unfilled"] = "unfilled"


class RegionRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route_id: str
    from_node_id: str
    to_node_id: str
    travel_modes: tuple[str, ...]
    distance_band: Literal["local", "near", "regional", "remote"]
    bidirectional: bool


class RegionSkeleton(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    setting_pack_id: str
    region_id: str
    pattern_id: str
    nodes: tuple[RegionNode, ...]
    routes: tuple[RegionRoute, ...]
    assumptions: tuple[str, ...]


def instantiate_region(
    pack: SettingPack,
    *,
    region_id: str,
    pattern_id: str,
    role_counts: Mapping[RegionNodeRole, int] | None = None,
) -> RegionSkeleton:
    """Materialize a bounded connected role graph, leaving every name unfilled."""

    normalized_id = region_id.strip()
    if not normalized_id or len(normalized_id) > 120:
        raise ValueError("Region id must contain 1-120 characters")
    pattern = pack.region_pattern(pattern_id)
    requested_counts = dict(role_counts or {})
    known_roles = {item.node_role for item in pattern.nodes}
    unknown_roles = set(requested_counts) - known_roles
    if unknown_roles:
        raise ValueError(
            "Region count references unknown roles: " + ", ".join(sorted(unknown_roles))
        )
    counts: dict[RegionNodeRole, int] = {}
    for template in pattern.nodes:
        count = requested_counts.get(template.node_role, template.minimum_count)
        if not template.minimum_count <= count <= template.maximum_count:
            raise ValueError(
                f"Region role {template.node_role} count must be between "
                f"{template.minimum_count} and {template.maximum_count}"
            )
        counts[template.node_role] = count
    nodes = tuple(
        RegionNode(
            node_id=f"{normalized_id}.{template.node_role}_{index}",
            node_role=template.node_role,
            settlement_kind=template.settlement_kind,
        )
        for template in pattern.nodes
        for index in range(1, counts[template.node_role] + 1)
    )
    by_role = {
        role: tuple(node for node in nodes if node.node_role == role)
        for role in {node.node_role for node in nodes}
    }
    routes = []
    for template in pattern.routes:
        starts = by_role.get(template.from_role, ())
        ends = by_role.get(template.to_role, ())
        if not starts or not ends:
            continue
        for index in range(max(len(starts), len(ends))):
            start = starts[index % len(starts)]
            end = ends[index % len(ends)]
            routes.append(
                RegionRoute(
                    route_id=f"{normalized_id}.route_{template.from_role}_{template.to_role}_{index + 1}",
                    from_node_id=start.node_id,
                    to_node_id=end.node_id,
                    travel_modes=template.travel_modes,
                    distance_band=template.distance_band,
                    bidirectional=template.bidirectional,
                )
            )
    adjacency: dict[str, set[str]] = {node.node_id: set() for node in nodes}
    for route in routes:
        adjacency[route.from_node_id].add(route.to_node_id)
        adjacency[route.to_node_id].add(route.from_node_id)
    reached = {nodes[0].node_id}
    frontier = list(reached)
    while frontier:
        current = frontier.pop()
        for target in adjacency[current] - reached:
            reached.add(target)
            frontier.append(target)
    if reached != set(adjacency):
        raise ValueError("Instantiated region graph is disconnected")
    return RegionSkeleton(
        setting_pack_id=pack.setting_pack_id,
        region_id=normalized_id,
        pattern_id=pattern.pattern_id,
        nodes=nodes,
        routes=tuple(routes),
        assumptions=("区域节点和路线只是待填写结构，不代表模组已经确认这些地点存在。",),
    )


def instantiate_settlement(
    pack: SettingPack,
    *,
    settlement_id: str,
    kind: SettlementKind,
    include_typical: bool = True,
    condition_tags: frozenset[str] = frozenset(),
) -> SettlementSkeleton:
    """Create stable empty slots without asserting names, NPCs, clues, or routes."""

    normalized_id = settlement_id.strip()
    if not normalized_id or len(normalized_id) > 120:
        raise ValueError("Settlement id must contain 1-120 characters")
    slots = []
    for item in pack.settlement(kind).slots:
        detail = pack.function_detail(item.slot_id)
        selected = (
            item.frequency == "core"
            or (item.frequency == "typical" and include_typical)
            or (item.frequency == "conditional" and bool(set(item.condition_tags) & condition_tags))
        )
        slots.append(
            InstantiatedSceneSlot(
                scene_id=f"{normalized_id}.{item.slot_id}",
                slot_id=item.slot_id,
                function=item.function,
                frequency=item.frequency,
                access_scope=item.access_scope,
                building_candidates=item.building_variants,
                play_affordances=item.play_affordances,
                interior_zones=detail.interior_zones if detail else (),
                profession_candidates=tuple(
                    profession
                    for profession in pack.professions
                    if detail and profession.profession_id in detail.profession_ids
                ),
                entity_archetype_candidates=tuple(
                    archetype
                    for archetype in pack.entity_archetypes
                    if item.slot_id in archetype.applicable_scene_slots
                ),
                service_capabilities=detail.service_capabilities if detail else (),
                record_sources=detail.record_sources if detail else (),
                access_patterns=detail.access_patterns if detail else (),
                investigation_surfaces=detail.investigation_surfaces if detail else (),
                event_seed_kinds=detail.event_seed_kinds if detail else (),
                selected=selected,
            )
        )
    return SettlementSkeleton(
        setting_pack_id=pack.setting_pack_id,
        settlement_id=normalized_id,
        settlement_kind=kind,
        scene_slots=tuple(slots),
        assumptions=(
            "场景槽位只是时代与聚落规模候选，尚未成为战役事实。",
            "具体名称、位置、NPC、路线和线索必须由模组证据或 KP 确认后填写。",
        ),
        era_constraints=pack.era_constraints,
        agent_workflow=pack.agent_workflow,
    )


__all__ = [
    "AccessScope",
    "AgentWorkflowStage",
    "EntityArchetype",
    "EntityArchetypeKind",
    "EntityRelationSlot",
    "EraConstraints",
    "InstantiatedSceneSlot",
    "ProfessionTemplate",
    "RegionNodeTemplate",
    "RegionPattern",
    "RegionRouteTemplate",
    "RegionSkeleton",
    "SceneFrequency",
    "SceneFunctionDetail",
    "SettingPack",
    "SettlementKind",
    "SettlementSceneSlot",
    "SettlementSkeleton",
    "SettlementTemplate",
    "instantiate_region",
    "instantiate_settlement",
]
