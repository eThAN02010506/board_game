"""Strict output contract for narration after deterministic checks resolve."""

import re
from collections import Counter
from collections.abc import Mapping, Sequence

from ai_kp.director.turn_output import (
    KpTurnOutput,
    StructuredOutputError,
    parse_kp_turn_output,
)
from ai_kp.platform.resolution import HIDDEN_CHECK_PUBLIC_NARRATION
from ai_kp.platform.resolution.narrative_safety import narration_claims_success

CHECK_CONSEQUENCE_OUTPUT_INSTRUCTIONS = """只返回一个 JSON 对象，不要 Markdown 或额外文字。你正在解释已经完成并验证的检定，不能要求新检定，也不能预改地图。字段必须是：
{
  "public_narration": "严格依据已验证检定结果给出的玩家可见后果",
  "kp_notes": "仅 KP 可见的简短备注",
  "action_ruling": {"goal":"原玩家行动的目标","method":"已经完成的技能或对抗","target":"行动对象","feasibility":"possible|partial","resolution":"automatic","reason":"如何依据已验证结果生成后果","maximum_effect":"该结果最多支持的效果","alternative":""},
  "proposed_checks": [],
  "proposed_events": [{"event_type":"类型","summary":"已经由结果支持的事实摘要","actor_type":"system|pc|npc|kp|environment","actor_id":null,"visibility":"player|table|kp","happened_at":null,"payload":{}}],
  "proposed_memories": [{"text":"值得长期记得且已由结果支持的内容","scope":"campaign_fact|pc_major|pc_side|npc_interaction|npc_relationship|location_fact|clue","importance":1,"visibility":"player|table|kp","pc_id":null,"npc_id":null,"happened_at":null}],
  "proposed_npc_updates": [{"npc_id":"上下文中明确存在的 NPC id","appeared":false,"relationship_delta":0,"last_seen_time":null,"note":""}],
  "proposed_map_moves": [],
  "proposed_facts": [{"fact_type":"canonical_fact|kp_secret|character_belief|rumor|ai_hypothesis","subject":"主体","predicate":"关系或属性","object_text":"已经由检定结果支持的内容","pc_id":null,"evidence_event_ids":[],"happened_at":null}],
  "proposed_world_entity_states": [{"entity_id":"上下文中明确存在的世界实体 id","expected_version":0,"dimension":"该实体 state_dimensions 中的稳定 id","value":"字符串、整数、布尔值或 null","visibility":"table|kp|secret","note":"由本次已验证结果支持的变化"}]
}
检定已经完成，因此 action_ruling.resolution 必须为 automatic；proposed_checks 和
proposed_map_moves 必须为空。不得改变骰值、成功等级、规则来源或 KP 覆盖；不得把失败
写成无代价成功。没有依据的效果必须省略。
校验结果决定后果方向：如果 verified_check_batch 中任一检定 failed（passed=false），
后果不得声称玩家成功发现、获得、说服或达成了任何东西；只能描述没有找到、没有达成、
维持原状或暴露失败本身。只有全部相关检定 passed 时，才能叙述发现/获得/成功。
proposed_facts 的 pc_id 只能用于 character_belief（某 PC 的认知）；canonical_fact、
kp_secret、rumor、ai_hypothesis 都不能带 pc_id。需要记录某 PC 的检定结果时，用
character_belief 或把该 PC 作为 subject 的文本，而不是给其他类别填 pc_id。
proposed_npc_updates 只能包含上下文中确实出现的 NPC；没有明确 NPC 时必须返回空数组，
不得编造或留空 npc_id（空 npc_id 会导致整份草稿被拒绝）。本场景的检定后果通常不需要
NPC 更新，除非检定本身涉及对话或关系的具体 NPC。
proposed_world_entity_states 只能记录本次已验证结果直接造成的变化，并严格复用上下文给出的
entity_id、state_version 和 state_dimensions；每个实体至多更新一个维度。
proposed_facts 只记录本次检定**新确立**的事实。如果该事实已经在团的世界事实中
（同一 subject + predicate），不要重复写入 proposed_facts，把它留在 public_narration
里即可；不要因为重复描述而创建冗余事实条目。
技能成功只表示玩家成功执行其手段，不会凭空决定观察对象采取了哪一种反应。目标的状态、
动机与反应必须来自 module_chunk、module_scene_baseline 或已确认世界事实；若依据只说明
目标保持静止，就叙述玩家确认其没有反应。不得用“微笑、点头或回话”这类未作选择的备选
列表代替一个具体、受依据支持的结果，也不得因成功骰自行新增 NPC 行为。
在生成后果前，逐项检查 module_chunk 中带“当/如果/一旦/除非”等条件的规则。若玩家的
行动声明、尝试本身或检定后确认的状态满足条件，必须同时叙述并记录原文明示的反应；该反应
不等同于玩家行动成功，不能因为玩家检定失败而省略。反之，原文没有触发反应时不得自行添加。
""".strip()

_HIDDEN_CHECK_OUTPUT_INSTRUCTIONS = f"""
当前 verified_check_batch 包含暗骰。暗骰的具体骰值、成功等级、通过与否及其直接后果只能留在 KP 可见内容中：
- public_narration 必须严格使用固定中性文本：{HIDDEN_CHECK_PUBLIC_NARRATION}
- proposed_events 与 proposed_memories 如非空，visibility 必须全部为 "kp"。
- proposed_facts 如非空，只能使用 "kp_secret" 或 "ai_hypothesis"。
- proposed_npc_updates 必须为空，因为该结构没有独立的 KP 可见性。
- proposed_world_entity_states 如非空，visibility 必须为 "kp" 或 "secret"。
- kp_notes 可以记录具体暗骰结果与裁定依据。
不得用同义改写、暗示、标点、数字或成功/失败措辞绕过固定公开文本。
""".strip()


def check_consequence_output_instructions(*, hidden_batch: bool = False) -> str:
    if not hidden_batch:
        return CHECK_CONSEQUENCE_OUTPUT_INSTRUCTIONS
    return (
        f"{CHECK_CONSEQUENCE_OUTPUT_INSTRUCTIONS}\n\n"
        f"{_HIDDEN_CHECK_OUTPUT_INSTRUCTIONS}"
    )


def enforce_check_consequence_privacy(
    output: KpTurnOutput,
    *,
    hidden_batch: bool = False,
) -> KpTurnOutput:
    if not hidden_batch:
        return output
    if any(event.visibility != "kp" for event in output.proposed_events):
        raise StructuredOutputError(
            "A hidden check consequence can only propose KP-visible events"
        )
    if any(memory.visibility != "kp" for memory in output.proposed_memories):
        raise StructuredOutputError(
            "A hidden check consequence can only propose KP-visible memories"
        )
    if output.proposed_npc_updates:
        raise StructuredOutputError(
            "A hidden check consequence cannot propose NPC updates"
        )
    if any(
        state.visibility == "table"
        for state in output.proposed_world_entity_states
    ):
        raise StructuredOutputError(
            "A hidden check consequence cannot propose table-visible entity states"
        )
    if any(
        fact.fact_type not in {"kp_secret", "ai_hypothesis"}
        for fact in output.proposed_facts
    ):
        raise StructuredOutputError(
            "A hidden check consequence can only propose KP-visible facts"
        )
    return output.model_copy(
        update={"public_narration": HIDDEN_CHECK_PUBLIC_NARRATION}
    )


# 检定失败时，叙事及结构化效果不得把这些词当作已达成/已获得。
# 只有当成功词以肯定语气出现（前面没有紧邻的否定词）才算声称成功，
# 避免把"没有发现""没找到"这类正确的失败描述误判为违规。
# Event types and payload values are machine-facing state, so use a deliberately
# narrow vocabulary here. Broad terms such as ``found`` are ambiguous
# (``guard_found_player`` is a valid failure consequence).
_SUCCESS_EVENT_TYPES = {
    "clue_discovered",
    "clue_found",
    "door_opened",
    "door_unlocked",
    "escape_succeeded",
    "item_acquired",
    "item_obtained",
    "npc_convinced",
    "npc_persuaded",
}
_SUCCESS_STATE_VALUES = {
    "acquired",
    "convinced",
    "discovered",
    "escaped",
    "found",
    "obtained",
    "opened",
    "persuaded",
    "success",
    "succeeded",
    "unlocked",
}
_SUCCESS_STATE_KEYS = {
    "acquired",
    "discovered",
    "escaped",
    "found",
    "obtained",
    "opened",
    "persuaded",
    "succeeded",
    "success",
    "unlocked",
}


def _event_persists_success(event_type: str, payload: object) -> bool:
    if event_type.strip().lower() in _SUCCESS_EVENT_TYPES:
        return True
    return _payload_persists_success(payload)


def _payload_persists_success(value: object) -> bool:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            if key in _SUCCESS_STATE_KEYS and child is True:
                return True
            if (
                key in {"state", "status", "result", "outcome"}
                and isinstance(child, str)
                and child.strip().lower() in _SUCCESS_STATE_VALUES
            ):
                return True
            if _payload_persists_success(child):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_payload_persists_success(child) for child in value)
    return False


def enforce_failure_consistency(
    output: KpTurnOutput,
    *,
    all_checks_passed: bool,
) -> KpTurnOutput:
    """任一相关检定失败时，拒绝声称成功的叙事或结构化效果。

    模型可能把公开叙事写成失败，却仍通过事件、记忆或事实提交成功
    效果。因此所有可落库文本都经过同一确定性兜底。多检定草稿目前
    没有逐效果关联字段，只有全部相关检定通过时才能提交成功型效果。
    """
    if all_checks_passed:
        return output
    if any(
        _event_persists_success(event.event_type, event.payload)
        for event in output.proposed_events
    ) or any(
        _payload_persists_success({"state": state.value})
        for state in output.proposed_world_entity_states
    ):
        raise StructuredOutputError(
            "A check consequence with a failed related check cannot claim or "
            "persist success via an event type or state transition"
        )
    effect_texts = [
        output.public_narration,
        *(event.summary for event in output.proposed_events),
        *(memory.text for memory in output.proposed_memories),
        *(update.note for update in output.proposed_npc_updates),
        *(
            f"{state.dimension} {state.value} {state.note}"
            for state in output.proposed_world_entity_states
        ),
        *(
            f"{fact.predicate} {fact.object_text}"
            for fact in output.proposed_facts
        ),
    ]
    if any(narration_claims_success(text) for text in effect_texts if text):
        raise StructuredOutputError(
            "A check consequence with a failed related check cannot claim or "
            "persist success, discovery, persuasion, or acquisition"
        )
    return output


def enforce_effect_ceiling_consistency(
    output: KpTurnOutput,
    *,
    forbidden_outcome_claims: tuple[str, ...],
) -> KpTurnOutput:
    """Enforce approved, action-scoped narrative constraints without scenario vocabulary."""
    if not forbidden_outcome_claims:
        return output
    texts = [
        output.public_narration,
        *(event.summary for event in output.proposed_events),
        *(memory.text for memory in output.proposed_memories),
        *(update.note for update in output.proposed_npc_updates),
        *(
            f"{state.dimension} {state.value} {state.note}"
            for state in output.proposed_world_entity_states
        ),
        *(fact.object_text for fact in output.proposed_facts),
    ]
    normalized_claims = tuple(
        "".join(claim.casefold().split()) for claim in forbidden_outcome_claims
    )
    if any(
        claim in "".join(text.casefold().split())
        for text in texts
        for claim in normalized_claims
        if text and claim
    ):
        raise StructuredOutputError(
            "A check consequence crossed an approved narrative effect constraint"
        )
    return output


def enforce_narrative_quality(output: KpTurnOutput) -> KpTurnOutput:
    """Reject obvious weak-model loops before they become table-visible history."""
    clauses = [
        re.sub(r"\s+", "", clause)
        for clause in re.split(r"[，。！？；,;\n]+", output.public_narration)
        if len(re.sub(r"\s+", "", clause)) >= 4
    ]
    if len(clauses) < 8:
        return output
    counts = Counter(clauses)
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    if max(counts.values(), default=0) >= 4 or repeated / len(clauses) >= 0.45:
        raise StructuredOutputError(
            "Check consequence narration contains excessive repeated clauses"
        )
    return output


def parse_check_consequence_output(
    raw: str,
    *,
    hidden_batch: bool = False,
) -> KpTurnOutput:
    output = parse_kp_turn_output(raw)
    if output.proposed_checks:
        raise StructuredOutputError(
            "A check consequence cannot request another check"
        )
    if output.proposed_map_moves:
        raise StructuredOutputError(
            "The first check-consequence slice cannot move map tokens"
        )
    return enforce_check_consequence_privacy(
        output,
        hidden_batch=hidden_batch,
    )


__all__ = [
    "CHECK_CONSEQUENCE_OUTPUT_INSTRUCTIONS",
    "check_consequence_output_instructions",
    "enforce_check_consequence_privacy",
    "enforce_effect_ceiling_consistency",
    "enforce_failure_consistency",
    "enforce_narrative_quality",
    "parse_check_consequence_output",
]
