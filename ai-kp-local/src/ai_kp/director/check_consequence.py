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
  "proposed_checks": [],
  "proposed_events": [{"event_type":"类型","summary":"已经由结果支持的事实摘要","actor_type":"system|pc|npc|kp|environment","actor_id":null,"visibility":"player|table|kp","happened_at":null,"payload":{}}],
  "proposed_memories": [{"text":"值得长期记得且已由结果支持的内容","scope":"campaign_fact|pc_major|pc_side|npc_interaction|npc_relationship|location_fact|clue","importance":1,"visibility":"player|table|kp","pc_id":null,"npc_id":null,"happened_at":null}],
  "proposed_npc_updates": [{"npc_id":"上下文中明确存在的 NPC id","appeared":false,"relationship_delta":0,"last_seen_time":null,"note":""}],
  "proposed_map_moves": [],
  "proposed_facts": [{"fact_type":"canonical_fact|kp_secret|character_belief|rumor|ai_hypothesis","subject":"主体","predicate":"关系或属性","object_text":"已经由检定结果支持的内容","pc_id":null,"evidence_event_ids":[],"happened_at":null}]
}
proposed_checks 和 proposed_map_moves 必须为空。不得改变骰值、成功等级、规则来源或 KP 覆盖；不得把失败写成无代价成功。没有依据的效果必须省略。
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
    "parse_check_consequence_output",
]
