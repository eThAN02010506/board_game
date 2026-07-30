import json
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from ai_kp.director.turn_output import StrictModel, StructuredOutputError, _extract_json


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

    @model_validator(mode="after")
    def require_plan_for_dynamic_expansion(self) -> "WorldExpansionCandidate":
        if self.expansion_kind in {"reactive_branch", "anchor_bridge"}:
            if self.branch_plan is None:
                raise ValueError(
                    "Reactive branches and anchor bridges require a branch_plan"
                )
            if self.expansion_kind == "anchor_bridge" and not (
                self.branch_plan.anchor_guards
            ):
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
    "branch_plan": null
  }
}
至少给出两个真正不同的替代方案。不得声称候选已经成为事实，不得编造 NPC、PC、地图、
棋子或模组实体 ID，不得提前揭示未解锁剧透。若候选可能改变剧情锚点，必须使用
anchor_bridge 并在 conflicts 或 assumptions 中说明风险。
environment 可令 branch_plan 为 null；reactive_branch 与 anchor_bridge 必须给出：
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
                f"{condition.condition_type}:{condition.reference} "
                f"failed {condition.operator}"
            )
    return failures


def parse_world_expansion_output(raw: str) -> WorldExpansionOutput:
    try:
        payload = json.loads(_extract_json(raw))
        return WorldExpansionOutput.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, StructuredOutputError) as exc:
        raise StructuredOutputError(str(exc)) from exc


def validate_world_expansion_plan(
    candidate: WorldExpansionCandidate,
    analysis_snapshot: dict[str, Any],
) -> None:
    """Reject invented graph/fact references before a branch draft is stored."""

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
    if (
        effect.effect_type == "entity_state_candidate"
        and effect.reference not in entity_ids
    ):
        raise StructuredOutputError(
            f"Branch effect references unknown entity: {effect.reference}"
        )


__all__ = [
    "WORLD_EXPANSION_OUTPUT_INSTRUCTIONS",
    "DynamicBranchPlan",
    "WorldExpansionCandidate",
    "WorldExpansionOutput",
    "branch_condition_failures",
    "parse_world_expansion_output",
    "validate_world_expansion_plan",
]
