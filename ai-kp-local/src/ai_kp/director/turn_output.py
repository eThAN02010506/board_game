"""Canonical validated proposal output contract for the AI KP director."""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Visibility = Literal["player", "table", "kp"]
AssertableFactType = Literal[
    "canonical_fact",
    "kp_secret",
    "character_belief",
    "rumor",
    "ai_hypothesis",
]
MemoryScope = Literal[
    "campaign_fact",
    "pc_major",
    "pc_side",
    "npc_interaction",
    "npc_relationship",
    "location_fact",
    "clue",
]
ActionFeasibility = Literal["possible", "partial", "impossible"]
ActionResolution = Literal["automatic", "check", "opposed", "no_roll"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CheckCandidate(StrictModel):
    skill: str = Field(min_length=1, max_length=100)
    difficulty: Literal["regular", "hard", "extreme", "opposed", "automatic"] = "regular"
    reason: str = Field(min_length=1, max_length=500)
    pc_id: str | None = None
    hidden: bool = False


class ActionRuling(StrictModel):
    """The declared goal, method, and effect ceiling decided before any roll."""

    goal: str = Field(min_length=1, max_length=500)
    method: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=300)
    feasibility: ActionFeasibility
    resolution: ActionResolution
    reason: str = Field(min_length=1, max_length=1000)
    maximum_effect: str = Field(min_length=1, max_length=1000)
    alternative: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def impossible_goals_do_not_roll(self) -> "ActionRuling":
        if self.feasibility == "impossible" and self.resolution != "no_roll":
            raise ValueError("An impossible action must not request a roll")
        if self.feasibility != "impossible" and self.resolution == "no_roll":
            raise ValueError("Only an impossible action may use no_roll")
        return self


class EventCandidate(StrictModel):
    event_type: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=2000)
    actor_type: Literal["system", "pc", "npc", "kp", "environment"] = "system"
    actor_id: str | None = None
    visibility: Visibility = "table"
    happened_at: str | None = None
    payload: dict = Field(default_factory=dict)


class MemoryCandidate(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    scope: MemoryScope = "campaign_fact"
    importance: int = Field(default=1, ge=1, le=5)
    visibility: Visibility = "table"
    pc_id: str | None = None
    npc_id: str | None = None
    happened_at: str | None = None


class NpcUpdateCandidate(StrictModel):
    npc_id: str = Field(min_length=1)
    appeared: bool = False
    relationship_delta: int = Field(default=0, ge=-10, le=10)
    last_seen_time: str | None = None
    note: str = Field(default="", max_length=1000)


class MapMoveCandidate(StrictModel):
    token_id: str = Field(min_length=1)
    to_location_name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    require_route: bool = True


class FactCandidate(StrictModel):
    fact_type: AssertableFactType
    subject: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=120)
    object_text: str = Field(min_length=1, max_length=4000)
    pc_id: str | None = Field(default=None, max_length=100)
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=20)
    happened_at: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_character_scope(self) -> "FactCandidate":
        if self.fact_type == "character_belief" and self.pc_id is None:
            raise ValueError("character_belief requires pc_id")
        if self.fact_type != "character_belief" and self.pc_id is not None:
            raise ValueError("Only character_belief can target a PC")
        return self


class KpTurnOutput(StrictModel):
    public_narration: str = Field(min_length=1, max_length=12000)
    kp_notes: str = Field(default="", max_length=4000)
    action_ruling: ActionRuling
    proposed_checks: list[CheckCandidate] = Field(default_factory=list, max_length=8)
    proposed_events: list[EventCandidate] = Field(default_factory=list, max_length=12)
    proposed_memories: list[MemoryCandidate] = Field(default_factory=list, max_length=10)
    proposed_npc_updates: list[NpcUpdateCandidate] = Field(default_factory=list, max_length=8)
    proposed_map_moves: list[MapMoveCandidate] = Field(default_factory=list, max_length=12)
    proposed_facts: list[FactCandidate] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def unresolved_checks_cannot_commit_dependent_effects(self) -> "KpTurnOutput":
        if self.proposed_checks and any(
            (
                self.proposed_events,
                self.proposed_memories,
                self.proposed_npc_updates,
                self.proposed_map_moves,
                self.proposed_facts,
            )
        ):
            raise ValueError(
                "A proposal requesting unresolved checks cannot also commit world effects"
            )
        has_effects = any(
            (
                self.proposed_events,
                self.proposed_memories,
                self.proposed_npc_updates,
                self.proposed_map_moves,
                self.proposed_facts,
            )
        )
        if self.action_ruling.feasibility == "impossible" and (
            self.proposed_checks or has_effects
        ):
            raise ValueError(
                "An impossible action cannot request dice or commit world effects"
            )
        if self.action_ruling.resolution in {"check", "opposed"}:
            if not self.proposed_checks:
                raise ValueError("A rolled action ruling requires proposed checks")
            if self.action_ruling.resolution == "opposed" and any(
                item.difficulty != "opposed" for item in self.proposed_checks
            ):
                raise ValueError("An opposed ruling requires opposed checks")
            if self.action_ruling.resolution == "check" and any(
                item.difficulty in {"opposed", "automatic"}
                for item in self.proposed_checks
            ):
                raise ValueError(
                    "A check ruling requires regular, hard, or extreme checks"
                )
        elif self.proposed_checks:
            raise ValueError(
                "Automatic and no-roll rulings cannot request proposed checks"
            )
        return self


STRUCTURED_OUTPUT_INSTRUCTIONS = """只返回一个 JSON 对象，不要 Markdown 或额外文字。字段必须是：
{
  "public_narration": "玩家可见的场景反馈",
  "kp_notes": "仅 KP 可见的简短备注",
  "action_ruling": {"goal":"玩家希望达成什么","method":"使用的手段","target":"目标对象","feasibility":"possible|partial|impossible","resolution":"automatic|check|opposed|no_roll","reason":"为何这样裁定","maximum_effect":"即使大成功也不会超过的效果上限","alternative":"不可行或部分可行时可尝试的替代做法"},
  "proposed_checks": [{"skill":"技能名","difficulty":"regular|hard|extreme|opposed|automatic","reason":"原因","pc_id":null,"hidden":false}],
  "proposed_events": [{"event_type":"类型","summary":"事实摘要","actor_type":"system|pc|npc|kp|environment","actor_id":null,"visibility":"player|table|kp","happened_at":null,"payload":{}}],
  "proposed_memories": [{"text":"值得长期记得的事实","scope":"campaign_fact|pc_major|pc_side|npc_interaction|npc_relationship|location_fact|clue","importance":1,"visibility":"player|table|kp","pc_id":null,"npc_id":null,"happened_at":null}],
  "proposed_npc_updates": [{"npc_id":"已知 NPC id","appeared":false,"relationship_delta":0,"last_seen_time":null,"note":""}],
  "proposed_map_moves": [{"token_id":"已知棋子 id","to_location_name":"目标地点","reason":"移动原因","require_route":true}],
  "proposed_facts": [{"fact_type":"canonical_fact|kp_secret|character_belief|rumor|ai_hypothesis","subject":"主体","predicate":"关系或属性","object_text":"值或陈述","pc_id":null,"evidence_event_ids":[],"happened_at":null}]
}
没有的候选项必须返回空数组。不得编造 NPC id、PC id、事件 id 或棋子 id。
先判断目标与手段是否在当前事实、目标能力和空间位置下可行，再决定是否掷骰。
骰子只能决定已声明可行目标的结果，01 或大成功不能突破 maximum_effect、赋予目标
不存在的交流或恐惧能力、跨越未连接地点或改写 module_canon。目标不可能时必须使用
feasibility=impossible、resolution=no_roll，解释原因并给出可选的目标重述；不得为了
“给玩家机会”虚构一次无意义检定。
社交行动必须区分“角色说了什么”与“世界事实是否为真”：冒充亲属、索要钥匙等通常是
话术/说服/魅惑与目标心理学或立场的裁定，成功至多令目标暂时相信或让步，不能把亲属关系
写成 canonical_fact。对白或具体说辞不足时，使用 impossible/no_roll 并在 alternative 中提出
一个具体 RP/澄清问题。玩家选择的技能必须来自其角色卡；不要发明“感知”等不存在技能。
从行驶列车跳下等固有危险行动可以尝试，但 maximum_effect 不得承诺安全；应先以灵感/INT
确认角色是否理解风险，再由后续行动处理跳跃/DEX与伤害。历史时代不存在的物品或服务不能
靠检定生成。
只有已经在当前场景中成立、并且值得作为长期真相区分检索的内容才进入 proposed_facts；
传闻必须标为 rumor，角色个人认知必须标为 character_belief，推测必须标为 ai_hypothesis。
proposed_facts 的 pc_id 只能用于 character_belief；canonical_fact、kp_secret、rumor、
ai_hypothesis 都不能带 pc_id，否则整份草稿会被拒绝。需要表达某 PC 的状态或认知时，
用 character_belief，或把该 PC 作为 subject 的文本，而不是给其他类别填 pc_id。
如果 proposed_checks 非空，则 proposed_events、proposed_memories、proposed_npc_updates、
proposed_map_moves 和 proposed_facts 必须全部为空；检定结果不得预写。
""".strip()


class StructuredOutputError(ValueError):
    pass


def _extract_json(raw: str) -> str:
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{") or not text.endswith("}"):
        raise StructuredOutputError("model output is not a JSON object")
    return text


def parse_kp_turn_output(raw: str) -> KpTurnOutput:
    try:
        payload = json.loads(_extract_json(raw))
        return KpTurnOutput.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, StructuredOutputError) as exc:
        raise StructuredOutputError(str(exc)) from exc


def dump_candidates(values: list[BaseModel | dict], model_type: type[BaseModel]) -> list[dict]:
    return [model_type.model_validate(value).model_dump(mode="json") for value in values]
