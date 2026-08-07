"""Strict output contract for narration after deterministic checks resolve."""

from ai_kp.director.turn_output import (
    KpTurnOutput,
    StructuredOutputError,
    parse_kp_turn_output,
)
from ai_kp.platform.resolution import HIDDEN_CHECK_PUBLIC_NARRATION

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
  "proposed_facts": [{"fact_type":"canonical_fact|kp_secret|character_belief|rumor|ai_hypothesis","subject":"主体","predicate":"关系或属性","object_text":"已经由检定结果支持的内容","pc_id":null,"evidence_event_ids":[],"happened_at":null}]
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
""".strip()

_HIDDEN_CHECK_OUTPUT_INSTRUCTIONS = f"""
当前 verified_check_batch 包含暗骰。暗骰的具体骰值、成功等级、通过与否及其直接后果只能留在 KP 可见内容中：
- public_narration 必须严格使用固定中性文本：{HIDDEN_CHECK_PUBLIC_NARRATION}
- proposed_events 与 proposed_memories 如非空，visibility 必须全部为 "kp"。
- proposed_facts 如非空，只能使用 "kp_secret" 或 "ai_hypothesis"。
- proposed_npc_updates 必须为空，因为该结构没有独立的 KP 可见性。
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
        fact.fact_type not in {"kp_secret", "ai_hypothesis"}
        for fact in output.proposed_facts
    ):
        raise StructuredOutputError(
            "A hidden check consequence can only propose KP-visible facts"
        )
    return output.model_copy(
        update={"public_narration": HIDDEN_CHECK_PUBLIC_NARRATION}
    )


# 检定失败时，后果叙事不得把这些词当作已达成/已获得。
# 只有当成功词以肯定语气出现（前面没有紧邻的否定词）才算声称成功，
# 避免把"没有发现""没找到"这类正确的失败描述误判为违规。
_SUCCESS_CLAIM_PATTERNS = (
    ("发现了", "发现"),
    ("发现", "发现"),
    ("找到了", "找到"),
    ("找到", "找到"),
    ("获得了", "获得"),
    ("拿到", "拿到"),
    ("取得", "取得"),
    ("成功", "成功"),
    ("达成了", "达成"),
    ("说服", "说服"),
    ("吓走", "吓走"),
    ("击退", "击退"),
    ("打开了", "打开"),
)
_NEGATION_PREFIXES = ("没有", "没", "未", "无法", "未能", "并未", "不曾", "并不")


def _claims_success(narration: str) -> bool:
    import re

    for word, _label in _SUCCESS_CLAIM_PATTERNS:
        for match in re.finditer(re.escape(word), narration):
            start = match.start()
            if start == 0:
                return True
            # 检查成功词之前最近的一小段是否被否定。
            preceding = narration[max(0, start - 3) : start]
            if any(preceding.endswith(neg) for neg in _NEGATION_PREFIXES):
                continue
            return True
    return False


def enforce_failure_consistency(
    output: KpTurnOutput,
    *,
    any_check_passed: bool,
) -> KpTurnOutput:
    """检定全部失败时，拒绝声称成功发现/获得/达成的后果叙事。

    这是确定性兜底：模型可能忽略提示词里"失败不得写成成功"的要求，
    因此这里检查叙事是否把失败写成了有奖励的结果。只有全部检定都
    失败（无通过）时才启用；部分成功时由模型决定哪些成果成立。
    """
    if any_check_passed:
        return output
    if _claims_success(output.public_narration):
        raise StructuredOutputError(
            "A failed check consequence cannot claim the player succeeded, "
            "found, or obtained something"
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
    "enforce_failure_consistency",
    "parse_check_consequence_output",
]
