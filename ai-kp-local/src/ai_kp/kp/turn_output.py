import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


Visibility = Literal["player", "table", "kp"]
MemoryScope = Literal[
    "campaign_fact",
    "pc_major",
    "pc_side",
    "npc_interaction",
    "npc_relationship",
    "location_fact",
    "clue",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckCandidate(StrictModel):
    skill: str = Field(min_length=1, max_length=100)
    difficulty: Literal["regular", "hard", "extreme", "opposed", "automatic"] = "regular"
    reason: str = Field(min_length=1, max_length=500)
    pc_id: str | None = None
    hidden: bool = False


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


class KpTurnOutput(StrictModel):
    public_narration: str = Field(min_length=1, max_length=12000)
    kp_notes: str = Field(default="", max_length=4000)
    proposed_checks: list[CheckCandidate] = Field(default_factory=list, max_length=8)
    proposed_events: list[EventCandidate] = Field(default_factory=list, max_length=12)
    proposed_memories: list[MemoryCandidate] = Field(default_factory=list, max_length=10)
    proposed_npc_updates: list[NpcUpdateCandidate] = Field(default_factory=list, max_length=8)
    proposed_map_moves: list[MapMoveCandidate] = Field(default_factory=list, max_length=12)


STRUCTURED_OUTPUT_INSTRUCTIONS = """只返回一个 JSON 对象，不要 Markdown 或额外文字。字段必须是：
{
  "public_narration": "玩家可见的场景反馈",
  "kp_notes": "仅 KP 可见的简短备注",
  "proposed_checks": [{"skill":"技能名","difficulty":"regular|hard|extreme|opposed|automatic","reason":"原因","pc_id":null,"hidden":false}],
  "proposed_events": [{"event_type":"类型","summary":"事实摘要","actor_type":"system|pc|npc|kp|environment","actor_id":null,"visibility":"player|table|kp","happened_at":null,"payload":{}}],
  "proposed_memories": [{"text":"值得长期记得的事实","scope":"campaign_fact|pc_major|pc_side|npc_interaction|npc_relationship|location_fact|clue","importance":1,"visibility":"player|table|kp","pc_id":null,"npc_id":null,"happened_at":null}],
  "proposed_npc_updates": [{"npc_id":"已知 NPC id","appeared":false,"relationship_delta":0,"last_seen_time":null,"note":""}],
  "proposed_map_moves": [{"token_id":"已知棋子 id","to_location_name":"目标地点","reason":"移动原因","require_route":true}]
}
没有的候选项必须返回空数组。不得编造 NPC id、PC id 或棋子 id。
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
