"""Transport normalization and prompts for the untrusted intent model boundary."""

from __future__ import annotations

import json
from copy import deepcopy

from ai_kp.platform.ports.llm import ChatMessage
from ai_kp.platform.resolution.action_intent_ir import ActionIntentPlan
from ai_kp.platform.resolution.contracts import ScenarioContract, ScenarioSnapshot
from ai_kp.platform.resolution.effect_catalog import ScenarioEffectCatalog


def normalize_intent_transport(payload: object, *, prefix: str) -> object:
    """Normalize harmless JSON spellings without inventing action semantics."""

    if not isinstance(payload, dict):
        return payload
    normalized = deepcopy(payload)
    comparisons = {
        "=": "eq", "==": "eq", "equal": "eq", "equals": "eq", "!=": "ne",
        ">": "gt", ">=": "gte", "<": "lt", "<=": "lte",
        "greater_than": "gt", "greater_than_or_equal": "gte",
        "less_than": "lt", "less_than_or_equal": "lte",
        "not_null": "exists", "not null": "exists",
    }
    for step in normalized.get("steps", []):
        if not isinstance(step, dict):
            continue
        step_id = step.get("step_id")
        if not step.get("operator_id") and isinstance(step_id, str):
            step["operator_id"] = prefix + step_id
        _normalize_requirements(step, comparisons)
        _normalize_effects(step)
    return normalized


def _normalize_requirements(
    step: dict[str, object], comparisons: dict[str, str]
) -> None:
    requirements = step.get("requirements", [])
    if not isinstance(requirements, list):
        return
    for index, requirement in enumerate(requirements, start=1):
        if not isinstance(requirement, dict):
            continue
        source_type = requirement.pop("type", None)
        if source_type in {"state", "prior_step", "player_input", "unsupported"}:
            current_source = requirement.get("source")
            if source_type == "state" and isinstance(current_source, str) and (
                "." in current_source or "/" in current_source
            ):
                requirement.setdefault("state_path", current_source)
                requirement["source"] = "state"
            else:
                requirement.setdefault("source", source_type)
        requirement.setdefault("requirement_id", f"requirement_{index}")
        requirement.setdefault("category", "external_condition")
        requirement.setdefault(
            "description",
            requirement.get("question") or "required external condition",
        )
        comparison = requirement.get("comparison")
        if isinstance(comparison, str):
            requirement["comparison"] = comparisons.get(
                comparison.strip().lower(), comparison
            )
        if requirement.get("question") is None:
            requirement["question"] = ""
        if requirement.get("player_evidence") is None:
            requirement["player_evidence"] = ""
        path = requirement.get("state_path")
        if isinstance(path, str) and "/" in path:
            requirement["state_path"] = path.replace("/", ".")
        if requirement.get("comparison") in {"gt", "gte", "lt", "lte"}:
            _normalize_numeric_value(requirement)


def _normalize_numeric_value(requirement: dict[str, object]) -> None:
    value = requirement.get("expected_value")
    if not isinstance(value, str):
        return
    try:
        numeric = float(value)
    except ValueError:
        return
    requirement["expected_value"] = int(numeric) if numeric.is_integer() else numeric


def _normalize_effects(step: dict[str, object]) -> None:
    effects = step.get("effects", [])
    if not isinstance(effects, list):
        return
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        target = effect.get("target_ref")
        kind = effect.get("command_kind")
        if kind == "advance_clock" and isinstance(target, str) and target.startswith(
            "clocks/"
        ):
            effect["target_ref"] = target.removeprefix("clocks/")
        elif kind == "adjust_resource" and isinstance(
            target, str
        ) and target.startswith("resources/"):
            effect["target_ref"] = target.removeprefix("resources/")


def intent_messages(
    contract: ScenarioContract,
    snapshot: ScenarioSnapshot,
    player_intent: str,
    proposal_id: str,
    allowed_skill_keys: tuple[str, ...],
    effect_catalog: ScenarioEffectCatalog | None,
    errors: list[str],
) -> list[ChatMessage]:
    prefix = f"expansion.{proposal_id}."
    state = {
        "status": snapshot.status, "scene_id": snapshot.scene_id,
        "facts": snapshot.facts, "entities": snapshot.entities,
        "actor_locations": snapshot.actor_locations, "resources": snapshot.resources,
        "clocks": snapshot.clocks,
        "known_locations": [item.location_id for item in contract.locations],
    }
    effects = (
        [entry.model_dump(mode="json") for entry in effect_catalog.entries]
        if effect_catalog is not None else []
    )
    skeleton = {
        "goal": "玩家希望在当前世界中达成的最终结果",
        "steps": [{
            "step_id": "step_1", "operator_id": prefix + "step_1",
            "goal": "本步骤的局部目标", "method": "玩家实际采用的手段",
            "target": "手段作用对象", "depends_on": [], "requirements": [],
            "effects": [{
                "effect_id": "effect_1", "role": "progress",
                "applies_on": "success",
                "command_kind": "set_fact", "target_ref": prefix + "step_1_result",
                "value": True,
                "description": "成功时产生的有界权威效果",
            }],
        }],
    }
    correction = f"\n上次结构错误：{errors[-1]}" if errors else ""
    return [
        ChatMessage(role="system", content=(
            "你只负责把任意玩家行动解析为通用 ActionIntentPlan JSON，不写叙事、"
            "不写契约记录、不决定骰点。不得依据示例动作套规则，也不得把玩家陈述"
            "当成已经成立的世界事实。"
        )),
        ChatMessage(role="user", content=(
            f"玩家原文：{player_intent}\nnamespace：{prefix}\n"
            f"当前权威状态：{json.dumps(state, ensure_ascii=False)}\n"
            f"玩家可选技能键：{json.dumps(allowed_skill_keys, ensure_ascii=False)}\n"
            f"已安装规则效果：{json.dumps(effects, ensure_ascii=False)}\n"
            "完整拆分语义步骤；每步用 namespace 内唯一 operator_id。无外部条件时"
            "requirements 必须为空，不得虚构位置、占位符、资源或事实。source 只能是 "
            "state、prior_step、player_input 或 unsupported。state 必须绑定当前状态；引用"
            "resources/entities/facts 时 player_evidence 必须逐字摘录玩家选择它的原文，"
            "未明确选择时改用 player_input。纯观察是读状态，不生成 set_fact。effects 只列出"
            "真实状态变化，command_kind 使用闭集：set_fact、set_entity_status、move_actor、"
            "adjust_resource、advance_clock、set_scene、emit_event、apply_ruleset_effect。applies_on "
            "只能是 always、success、failure 或 pushed_failure；任意检定必须声明"
            "普通失败的有界权威后果，无法合理确定时用 player_input requirement 询问玩家。"
            "每个 effect 必须冻结唯一完整命令：target_ref 不得缺失；set_fact 与 "
            "set_entity_status 必须给出精确 value；adjust_resource 与 advance_clock "
            "必须给出精确 delta；emit_event 与 apply_ruleset_effect 的 payload "
            "必须是完整参数（无参数时为空对象）；move_actor 必须以 actor_id='$actor' "
            "绑定当前行动者，target_ref 是目标位置。时间经过用 advance_clock，"
            "时间/资源 target_ref 分别是 clock_id/resource_id。缺少必需规则效果"
            "时用 unsupported requirement。只返回 {goal,steps}，顶层与步骤字段不得增删；"
            "effect 只按 command_kind 添加上述对应的命令参数字段："
            f"{json.dumps(skeleton, ensure_ascii=False)}" + correction
        )),
    ]


def audit_messages(
    contract: ScenarioContract,
    snapshot: ScenarioSnapshot,
    player_intent: str,
    allowed_skill_keys: tuple[str, ...],
    effect_catalog: ScenarioEffectCatalog | None,
    intent_plan: ActionIntentPlan,
    errors: list[str],
) -> list[ChatMessage]:
    authority = {
        "status": snapshot.status, "scene_id": snapshot.scene_id,
        "facts": snapshot.facts, "entities": snapshot.entities,
        "actor_locations": snapshot.actor_locations, "resources": snapshot.resources,
        "clocks": snapshot.clocks,
        "locations": [item.location_id for item in contract.locations],
        "skills_explicitly_selected": allowed_skill_keys,
        "installed_ruleset_effects": (
            [entry.model_dump(mode="json") for entry in effect_catalog.entries]
            if effect_catalog is not None else []
        ),
    }
    correction = f"\n上次结构错误：{errors[-1]}" if errors else ""
    return [
        ChatMessage(role="system", content=(
            "你是独立的行动意图完整性审计器，只返回 JSON。不能改写计划、写叙事或授权，"
            "不得依赖动作关键词或预设场景范例。"
        )),
        ChatMessage(role="user", content=(
            f"玩家原文：{player_intent}\n只读权威：{json.dumps(authority, ensure_ascii=False)}\n"
            f"待审计计划：{intent_plan.model_dump_json(exclude_none=True, exclude_defaults=True)}\n"
            "核对计划是否捏造数量/手段/资源/关系/事实，是否遗漏目标必需前置和后果，"
            "state 是否真满足，effect 是否能实际表达结果（不得用布尔事实伪装广泛变更），"
            "规则效果是否已安装。完整且不越权才 accept；可补充缺失项返回 clarification 和"
            "questions；权威不存在返回 impossible 和 issues。不得返回修正计划。严格输出 "
            "{verdict,reason,issues,questions}。" + correction
        )),
    ]


__all__ = ["audit_messages", "intent_messages", "normalize_intent_transport"]
