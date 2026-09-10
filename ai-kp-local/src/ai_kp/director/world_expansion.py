import json
import re
from collections import Counter
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from ai_kp.director.turn_output import StrictModel, StructuredOutputError
from ai_kp.platform.structured_json import decode_json_object


class WorldExpansionAlternative(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    tradeoff: str = Field(min_length=1, max_length=600)


class BranchCondition(StrictModel):
    condition_type: Literal["entity_state", "world_fact", "scene", "time"]
    reference: str = Field(min_length=1, max_length=200)
    operator: Literal["equals", "not_equals", "exists", "not_exists"] = "equals"
    expected: str | None = Field(default=None, max_length=500)
    rationale: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def require_expected_value(self) -> "BranchCondition":
        if self.operator in {"equals", "not_equals"} and self.expected is None:
            raise ValueError(f"{self.operator} requires an expected value")
        if self.condition_type == "scene" and self.reference not in {
            "current_scene_key",
            "current_scene_title",
        }:
            raise ValueError("Scene conditions require a supported scene field")
        if self.condition_type == "time" and self.reference != "current_time":
            raise ValueError("Time conditions must reference current_time")
        return self


class BranchEffect(StrictModel):
    effect_type: Literal[
        "narrative_only",
        "entity_state_candidate",
        "fact_candidate",
        "scene_candidate",
    ]
    reference: str | None = Field(default=None, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    requires_contact: bool = True


class BranchBeat(StrictModel):
    beat_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=200)
    character_intent: str = Field(min_length=1, max_length=600)
    action: str = Field(min_length=1, max_length=1200)
    preconditions: list[BranchCondition] = Field(default_factory=list, max_length=8)
    expected_effects: list[BranchEffect] = Field(min_length=1, max_length=8)
    failure_policy: Literal["skip", "divert", "pause_for_kp"]


class AnchorGuard(StrictModel):
    anchor_entity_id: str = Field(min_length=1, max_length=200)
    invariant: str = Field(min_length=1, max_length=1000)
    recovery: str = Field(min_length=1, max_length=1000)


class DynamicBranchPlan(StrictModel):
    goal: str = Field(min_length=1, max_length=1000)
    entry_conditions: list[BranchCondition] = Field(min_length=1, max_length=10)
    beats: list[BranchBeat] = Field(min_length=1, max_length=8)
    anchor_guards: list[AnchorGuard] = Field(default_factory=list, max_length=8)
    completion_conditions: list[BranchCondition] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_beat_ids(self) -> "DynamicBranchPlan":
        ids = [item.beat_id for item in self.beats]
        if len(ids) != len(set(ids)):
            raise ValueError("Dynamic branch beat IDs must be unique")
        return self


class EntityRelationBinding(StrictModel):
    relation_slot_id: str = Field(min_length=1, max_length=64)
    target_source: Literal["candidate", "existing_entity"]
    target_ref: str = Field(min_length=1, max_length=200)


class EntityTemplateBinding(StrictModel):
    """A locally named archetype choice; still only part of an unapproved candidate."""

    local_ref: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    archetype_id: str = Field(min_length=1, max_length=64)
    entity_kind: Literal[
        "npc", "organization", "item", "document", "vehicle", "event", "clue_carrier"
    ]
    label_variant: str = Field(min_length=1, max_length=160)
    profession_ids: list[str] = Field(default_factory=list, max_length=12)
    relation_bindings: list[EntityRelationBinding] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def unique_template_choices(self) -> "EntityTemplateBinding":
        if len(self.profession_ids) != len(set(self.profession_ids)):
            raise ValueError("Entity template professions must be unique")
        relations = [
            (item.relation_slot_id, item.target_source, item.target_ref)
            for item in self.relation_bindings
        ]
        if len(relations) != len(set(relations)):
            raise ValueError("Entity template relations must be unique")
        return self


class WorldTemplateBinding(StrictModel):
    setting_pack_id: str = Field(min_length=1, max_length=80)
    settlement_kind: Literal["city", "town", "village", "rural"]
    slot_id: str = Field(min_length=1, max_length=64)
    building_variant: str = Field(min_length=1, max_length=240)
    profession_ids: list[str] = Field(default_factory=list, max_length=12)
    source_entity_ids: list[str] = Field(default_factory=list, max_length=24)
    entity_bindings: list[EntityTemplateBinding] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def unique_template_references(self) -> "WorldTemplateBinding":
        if len(self.profession_ids) != len(set(self.profession_ids)):
            raise ValueError("World template professions must be unique")
        if len(self.source_entity_ids) != len(set(self.source_entity_ids)):
            raise ValueError("World template source entity IDs must be unique")
        local_refs = [item.local_ref for item in self.entity_bindings]
        if len(local_refs) != len(set(local_refs)):
            raise ValueError("World template entity local refs must be unique")
        return self


class WorldExpansionCandidate(StrictModel):
    expansion_kind: Literal["environment", "reactive_branch", "anchor_bridge"]
    subject: str = Field(min_length=1, max_length=300)
    proposal: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)
    confidence: Literal["low", "medium", "high"]
    assumptions: list[str] = Field(default_factory=list, max_length=8)
    conflicts: list[str] = Field(default_factory=list, max_length=8)
    alternatives: list[WorldExpansionAlternative] = Field(min_length=2, max_length=5)
    branch_plan: DynamicBranchPlan | None = None
    template_binding: WorldTemplateBinding | None = None

    @model_validator(mode="after")
    def require_plan_for_dynamic_expansion(self) -> "WorldExpansionCandidate":
        if self.expansion_kind in {"reactive_branch", "anchor_bridge"}:
            if self.branch_plan is None:
                raise ValueError("Reactive branches and anchor bridges require a branch_plan")
            if self.expansion_kind == "anchor_bridge" and not (self.branch_plan.anchor_guards):
                raise ValueError("An anchor bridge requires at least one anchor guard")
        return self


class WorldExpansionOutput(StrictModel):
    public_narration: str = Field(min_length=1, max_length=12000)
    kp_notes: str = Field(default="", max_length=4000)
    candidate: WorldExpansionCandidate


WORLD_EXPANSION_OUTPUT_INSTRUCTIONS = """只返回一个 JSON 对象，不要 Markdown 或额外文字：
{
  "public_narration": "仅在 KP 批准后才会公开的场景描述",
  "kp_notes": "仅 KP 可见的风险和使用建议",
    "candidate": {
    "expansion_kind": "environment|reactive_branch|anchor_bridge",
    "subject": "补全对象",
    "proposal": "建议采用的世界补全",
    "rationale": "为何符合当前时代、地点、场景和已确认事实",
    "confidence": "low|medium|high",
    "assumptions": ["仍需 KP 确认的假设"],
    "conflicts": ["与来源或现有事实的潜在冲突；没有则为空数组"],
    "alternatives": [
      {"title":"替代方案 A","description":"内容","tradeoff":"取舍"},
      {"title":"替代方案 B","description":"内容","tradeoff":"取舍"}
    ],
    "branch_plan": null,
    "template_binding": {
      "setting_pack_id": "分析快照中的 ID",
      "settlement_kind": "city|town|village|rural",
      "slot_id": "一个 selected=true 的槽位 ID",
      "building_variant": "该槽位的一个 building_candidates 值",
      "profession_ids": ["该槽位允许的职位 ID"],
      "source_entity_ids": ["需要复用的 source_entity_bindings.module_entity_id"],
      "entity_bindings": [{
        "local_ref": "候选包内唯一小写标识",
        "archetype_id": "该槽位允许的原型 ID",
        "entity_kind": "原型的 entity_kind",
        "label_variant": "原型的一个 label_variants 值",
        "profession_ids": ["该原型允许的职位 ID"],
        "relation_bindings": [{
          "relation_slot_id": "该原型的关系槽 ID",
          "target_source": "candidate|existing_entity",
          "target_ref": "local_ref 或快照已有 entity_id"
        }]
      }]
    }
  }
}
至少给出两个真正不同的替代方案。不得声称候选已经成为事实；不得编造 PC、地图、棋子或
模组实体 ID。新 NPC/组织/物件只能作为 entity_bindings 中的原型候选，不得冒充已有实体。
candidate.expansion_kind 必须严格等于分析快照的 requested_expansion_kind；这是执行内核选定的
工作流，不得自行升级为 reactive_branch 或 anchor_bridge，也不得降级为 environment。
不得提前揭示未解锁剧透。若候选可能改变剧情锚点，必须使用
anchor_bridge 并在 conflicts 或 assumptions 中说明风险。
若确定性分析快照含 settlement_template，则必须从 selected=true 的 scene_slots 中选择一个，
并在 template_binding 原样返回 setting_pack_id、settlement_kind、slot_id、一个
building_candidates 中的建筑变体，以及 profession_candidates 中实际需要的 profession_id；
可从 entity_archetype_candidates 选择所需实体，并在 entity_bindings 返回唯一 local_ref、
archetype_id、原样 entity_kind、一个 label_variants 值，以及该原型允许的 profession_id。
relation_bindings 只能使用该原型 relation_slots 中的 relation_slot_id；target_source=candidate
时 target_ref 指向本候选包 local_ref，existing_entity 时指向快照已有 entity_id，并须满足目标种类。
必需关系必须满足 minimum_count。不需要实体时返回空数组；不得自行创造目录外的建筑类型、职位或实体原型。
source_coverage.bindings 是本子已提及地点的匹配，
source_entity_bindings 是本子已有实体及其经 KP 确认的原型绑定，必须优先复用；复用项的
entity_id 放入 template_binding.source_entity_ids，不得再以 entity_bindings 复制同一实体。
missing_core_slot_ids 只是可补全候选，不能声称已经存在。
environment 必须令 branch_plan 为 null；只有 reactive_branch 与 anchor_bridge 才给出：
{"goal":"目标","entry_conditions":[{"condition_type":"entity_state|world_fact|scene|time",
"reference":"entity_state/world_fact 使用已有 ID/键；scene 仅使用 current_scene_key 或 "
"current_scene_title；time 仅使用 current_time","operator":
"equals|not_equals|exists|not_exists",
"expected":"期望值或 null","rationale":"原因"}],
"beats":[{"beat_id":"稳定小写标识","title":"节拍","character_intent":"NPC/环境为何行动",
"action":"发生什么","preconditions":[],"expected_effects":[{"effect_type":
"narrative_only|entity_state_candidate|fact_candidate|scene_candidate",
"reference":null,"description":"仅是候选效果","requires_contact":true}],
"failure_policy":"skip|divert|pause_for_kp"}],
"anchor_guards":[{"anchor_entity_id":"快照已有 anchor ID","invariant":"不得破坏的条件",
"recovery":"偏离时如何恢复可达"}],"completion_conditions":[...]}
支线效果只能是候选；实际事实、实体状态和场景变化仍必须走原有人工确认接口。""".strip()


def world_expansion_output_instructions(requested_kind: str) -> str:
    """Render the schema example with the server-selected workflow kind.

    Small models commonly copy enum notation verbatim. Replacing only this
    example token keeps the output contract concrete without weakening the
    deterministic validator.
    """

    if requested_kind not in {"environment", "reactive_branch", "anchor_bridge"}:
        raise ValueError(f"Unsupported world expansion kind: {requested_kind}")
    return WORLD_EXPANSION_OUTPUT_INSTRUCTIONS.replace(
        '"expansion_kind": "environment|reactive_branch|anchor_bridge"',
        f'"expansion_kind": "{requested_kind}"',
        1,
    )


def branch_condition_failures(
    conditions: list[BranchCondition],
    snapshot: dict[str, Any],
) -> list[str]:
    """Evaluate branch guards without producing any narrative side effects."""

    entity_values = {
        str(item["entity_id"]): item.get("status")
        for item in snapshot.get("entity_states") or []
        if item.get("entity_id")
    }
    fact_values = {
        str(item["fact_key"]): item.get("object_text")
        for item in snapshot.get("world_fact_heads") or []
        if item.get("fact_key")
    }
    scene = snapshot.get("scene") or {}
    time = snapshot.get("time") or {}
    sources = {
        "entity_state": entity_values,
        "world_fact": fact_values,
        "scene": scene,
        "time": time,
    }
    failures: list[str] = []
    for condition in conditions:
        source = sources[condition.condition_type]
        present = condition.reference in source and source[condition.reference] is not None
        actual = source.get(condition.reference)
        matched = {
            "exists": present,
            "not_exists": not present,
            "equals": present and str(actual) == condition.expected,
            "not_equals": not present or str(actual) != condition.expected,
        }[condition.operator]
        if not matched:
            failures.append(
                f"{condition.condition_type}:{condition.reference} failed {condition.operator}"
            )
    return failures


def parse_world_expansion_output(raw: str) -> WorldExpansionOutput:
    try:
        payload = decode_json_object(raw)
        return WorldExpansionOutput.model_validate(payload)
    except (ValueError, ValidationError) as exc:
        raise StructuredOutputError(str(exc)) from exc


def normalize_world_expansion_transport(
    raw: str,
    analysis_snapshot: dict[str, Any],
) -> tuple[str, bool]:
    """Repair authority-neutral transport mistakes without choosing game content."""

    try:
        payload = decode_json_object(raw)
    except ValueError:
        return raw, False
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict):
        return raw, False
    changed = False
    if not payload.get("public_narration") and isinstance(candidate.get("proposal"), str):
        payload["public_narration"] = candidate["proposal"]
        changed = True
    placeholder = "environment|reactive_branch|anchor_bridge"
    if candidate.get("expansion_kind") == placeholder:
        candidate["expansion_kind"] = analysis_snapshot.get(
            "requested_expansion_kind", "environment"
        )
        changed = True
    binding = candidate.get("template_binding")
    if not isinstance(binding, dict):
        return json.dumps(payload, ensure_ascii=False), changed
    template = analysis_snapshot.get("settlement_template") or {}
    allowed_source_ids = {
        str(item.get("module_entity_id"))
        for item in template.get("source_entity_bindings") or ()
        if item.get("module_entity_id")
    }
    source_ids = binding.get("source_entity_ids")
    if isinstance(source_ids, list):
        filtered_source_ids = [
            item for item in source_ids if isinstance(item, str) and item in allowed_source_ids
        ]
        if filtered_source_ids != source_ids:
            binding["source_entity_ids"] = filtered_source_ids
            changed = True
    entities = binding.get("entity_bindings")
    if not isinstance(entities, list):
        return json.dumps(payload, ensure_ascii=False), changed
    original_refs = [
        item.get("local_ref")
        for item in entities
        if isinstance(item, dict) and isinstance(item.get("local_ref"), str)
    ]
    ref_counts = Counter(original_refs)
    ref_map: dict[str, str] = {}
    used_refs = {ref for ref in original_refs if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", ref)}
    for index, entity in enumerate(entities, start=1):
        if not isinstance(entity, dict):
            continue
        old_ref = entity.get("local_ref")
        if not isinstance(old_ref, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", old_ref):
            continue
        new_ref = f"entity_{index}"
        while new_ref in used_refs:
            new_ref = f"{new_ref}_x"
        entity["local_ref"] = new_ref
        used_refs.add(new_ref)
        if ref_counts[old_ref] == 1:
            ref_map[old_ref] = new_ref
        changed = True
    local_refs = {
        str(item.get("local_ref"))
        for item in entities
        if isinstance(item, dict) and item.get("local_ref")
    }
    existing_refs = {
        str(item.get("entity_id"))
        for item in analysis_snapshot.get("entity_states") or ()
        if item.get("entity_id")
    } | allowed_source_ids
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        relations = entity.get("relation_bindings")
        if not isinstance(relations, list):
            continue
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            target_ref = relation.get("target_ref")
            if target_ref in ref_map:
                target_ref = ref_map[target_ref]
                relation["target_ref"] = target_ref
                changed = True
            expected_source = None
            if target_ref in local_refs:
                expected_source = "candidate"
            elif target_ref in existing_refs:
                expected_source = "existing_entity"
            if expected_source is not None and relation.get("target_source") != expected_source:
                relation["target_source"] = expected_source
                changed = True
    return json.dumps(payload, ensure_ascii=False), changed


def validate_world_expansion_plan(
    candidate: WorldExpansionCandidate,
    analysis_snapshot: dict[str, Any],
) -> None:
    """Reject invented graph/fact references before a branch draft is stored."""

    requested_kind = analysis_snapshot.get("requested_expansion_kind", "environment")
    if candidate.expansion_kind != requested_kind:
        raise StructuredOutputError(
            "World expansion kind changed the server-selected workflow: "
            f"expected {requested_kind}, got {candidate.expansion_kind}"
        )

    _validate_template_binding(candidate, analysis_snapshot)

    plan = candidate.branch_plan
    if plan is None:
        return
    entity_ids = {
        str(item["entity_id"])
        for item in analysis_snapshot.get("entity_states") or []
        if item.get("entity_id")
    }
    anchor_ids = {
        str(item["entity_id"])
        for item in analysis_snapshot.get("anchors") or []
        if item.get("entity_id")
    }
    fact_keys = {
        str(item["fact_key"])
        for item in analysis_snapshot.get("world_fact_heads") or []
        if item.get("fact_key")
    }
    conditions = [
        *plan.entry_conditions,
        *plan.completion_conditions,
        *(condition for beat in plan.beats for condition in beat.preconditions),
    ]
    for condition in conditions:
        _validate_condition_reference(condition, entity_ids, fact_keys)
    for guard in plan.anchor_guards:
        if guard.anchor_entity_id not in anchor_ids:
            raise StructuredOutputError(
                f"Branch guard references unknown anchor: {guard.anchor_entity_id}"
            )
    for beat in plan.beats:
        for effect in beat.expected_effects:
            _validate_effect_reference(effect, entity_ids)
    if candidate.expansion_kind == "anchor_bridge" and not anchor_ids:
        raise StructuredOutputError(
            "Anchor bridge cannot be proposed without an approved module anchor"
        )


def _validate_template_binding(
    candidate: WorldExpansionCandidate,
    analysis_snapshot: dict[str, Any],
) -> None:
    template = analysis_snapshot.get("settlement_template")
    binding = candidate.template_binding
    if template is None:
        if binding is not None:
            raise StructuredOutputError(
                "World template binding has no authoritative template snapshot"
            )
        return
    if binding is None:
        raise StructuredOutputError(
            "Selected settlement template requires a structured template binding"
        )
    if binding.setting_pack_id != template.get("setting_pack_id"):
        raise StructuredOutputError("World template binding changed the setting pack")
    if binding.settlement_kind != template.get("settlement_kind"):
        raise StructuredOutputError("World template binding changed the settlement kind")
    slot = next(
        (
            item
            for item in template.get("scene_slots") or ()
            if item.get("slot_id") == binding.slot_id
        ),
        None,
    )
    if slot is None or not slot.get("selected"):
        raise StructuredOutputError("World template binding references an unavailable scene slot")
    if binding.building_variant not in (slot.get("building_candidates") or ()):
        raise StructuredOutputError("World template binding invented a building variant")
    allowed_professions = {
        str(item.get("profession_id"))
        for item in slot.get("profession_candidates") or ()
        if item.get("profession_id")
    }
    unknown_professions = sorted(set(binding.profession_ids) - allowed_professions)
    if unknown_professions:
        raise StructuredOutputError(
            "World template binding invented professions: " + ", ".join(unknown_professions)
        )
    allowed_source_entities = {
        str(item.get("module_entity_id"))
        for item in template.get("source_entity_bindings") or ()
        if item.get("module_entity_id")
    }
    unknown_source_entities = sorted(
        set(binding.source_entity_ids) - allowed_source_entities
    )
    if unknown_source_entities:
        raise StructuredOutputError(
            "World template binding invented source entities: "
            + ", ".join(unknown_source_entities)
        )
    allowed_archetypes = {
        str(item.get("archetype_id")): item
        for item in slot.get("entity_archetype_candidates") or ()
        if item.get("archetype_id")
    }
    entities_by_ref = {item.local_ref: item for item in binding.entity_bindings}
    existing_entity_kinds = {
        str(item.get("entity_id")): _module_entity_archetype_kind(str(item.get("entity_type")))
        for item in analysis_snapshot.get("entity_states") or ()
        if item.get("entity_id")
    }
    existing_entity_kinds.update(
        {
            str(item.get("module_entity_id")): str(
                (item.get("archetype") or {}).get("entity_kind")
            )
            for item in template.get("source_entity_bindings") or ()
            if item.get("module_entity_id")
            and (item.get("archetype") or {}).get("entity_kind")
        }
    )
    for entity in binding.entity_bindings:
        archetype = allowed_archetypes.get(entity.archetype_id)
        if archetype is None:
            raise StructuredOutputError(
                f"World template binding invented entity archetype: {entity.archetype_id}"
            )
        if entity.entity_kind != archetype.get("entity_kind"):
            raise StructuredOutputError(
                f"World template binding changed entity kind: {entity.archetype_id}"
            )
        if entity.label_variant not in (archetype.get("label_variants") or ()):
            raise StructuredOutputError(
                f"World template binding invented entity label: {entity.archetype_id}"
            )
        allowed_entity_professions = set(archetype.get("profession_ids") or ())
        unknown_entity_professions = sorted(
            set(entity.profession_ids) - allowed_entity_professions
        )
        if unknown_entity_professions:
            raise StructuredOutputError(
                "World template binding invented entity professions: "
                + ", ".join(unknown_entity_professions)
            )
        relation_slots = {
            str(item.get("relation_slot_id")): item
            for item in archetype.get("relation_slots") or ()
            if item.get("relation_slot_id")
        }
        counts: dict[str, int] = {}
        for relation in entity.relation_bindings:
            relation_slot = relation_slots.get(relation.relation_slot_id)
            if relation_slot is None:
                allowed_relation_slots = ", ".join(sorted(relation_slots)) or "none"
                raise StructuredOutputError(
                    "World template binding invented relation slot for "
                    f"{entity.local_ref}: {relation.relation_slot_id}; "
                    f"allowed={allowed_relation_slots}"
                )
            counts[relation.relation_slot_id] = counts.get(relation.relation_slot_id, 0) + 1
            if relation.target_source == "candidate":
                target = entities_by_ref.get(relation.target_ref)
                target_kind = target.entity_kind if target else None
                target_archetype_id = target.archetype_id if target else None
            else:
                target_kind = existing_entity_kinds.get(relation.target_ref)
                target_archetype_id = None
            if target_kind is None:
                raise StructuredOutputError(
                    f"World template relation has unknown target: {relation.target_ref}"
                )
            if target_kind not in (relation_slot.get("target_kinds") or ()):
                raise StructuredOutputError(
                    f"World template relation {entity.local_ref}."
                    f"{relation.relation_slot_id} target {relation.target_ref} has kind "
                    f"{target_kind}; allowed={relation_slot.get('target_kinds') or []}"
                )
            restricted_targets = set(relation_slot.get("target_archetype_ids") or ())
            if (
                restricted_targets
                and relation.target_source == "candidate"
                and target_archetype_id not in restricted_targets
            ):
                raise StructuredOutputError(
                    f"World template relation {entity.local_ref}."
                    f"{relation.relation_slot_id} target {relation.target_ref} has archetype "
                    f"{target_archetype_id}; allowed={sorted(restricted_targets)}"
                )
        for relation_slot_id, relation_slot in relation_slots.items():
            count = counts.get(relation_slot_id, 0)
            minimum = int(relation_slot.get("minimum_count", 0))
            maximum = int(relation_slot.get("maximum_count", 1))
            if not minimum <= count <= maximum:
                raise StructuredOutputError(
                    f"World template relation cardinality failed: {entity.local_ref}."
                    f"{relation_slot_id} count={count}; allowed={minimum}..{maximum}"
                )


def _module_entity_archetype_kind(entity_type: str) -> str | None:
    return {
        "npc": "npc",
        "organization": "organization",
        "item": "item",
        "clue": "clue_carrier",
        "event": "event",
    }.get(entity_type)


def _validate_condition_reference(
    condition: BranchCondition,
    entity_ids: set[str],
    fact_keys: set[str],
) -> None:
    known_references = {
        "entity_state": entity_ids,
        "world_fact": fact_keys,
    }
    allowed = known_references.get(condition.condition_type)
    if allowed is None or condition.reference in allowed:
        return
    label = "entity" if condition.condition_type == "entity_state" else "world fact"
    raise StructuredOutputError(
        f"Branch condition references unknown {label}: {condition.reference}"
    )


def _validate_effect_reference(
    effect: BranchEffect,
    entity_ids: set[str],
) -> None:
    if effect.effect_type == "entity_state_candidate" and effect.reference not in entity_ids:
        raise StructuredOutputError(f"Branch effect references unknown entity: {effect.reference}")


__all__ = [
    "WORLD_EXPANSION_OUTPUT_INSTRUCTIONS",
    "DynamicBranchPlan",
    "EntityRelationBinding",
    "EntityTemplateBinding",
    "WorldExpansionCandidate",
    "WorldExpansionOutput",
    "WorldTemplateBinding",
    "branch_condition_failures",
    "normalize_world_expansion_transport",
    "parse_world_expansion_output",
    "validate_world_expansion_plan",
    "world_expansion_output_instructions",
]
