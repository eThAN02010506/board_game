import json
from typing import Literal

from pydantic import Field, ValidationError

from ai_kp.director.turn_output import StrictModel, StructuredOutputError, _extract_json


class WorldExpansionAlternative(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    tradeoff: str = Field(min_length=1, max_length=600)


class WorldExpansionCandidate(StrictModel):
    expansion_kind: Literal["environment", "reactive_branch", "anchor_bridge"]
    subject: str = Field(min_length=1, max_length=300)
    proposal: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)
    confidence: Literal["low", "medium", "high"]
    assumptions: list[str] = Field(default_factory=list, max_length=8)
    conflicts: list[str] = Field(default_factory=list, max_length=8)
    alternatives: list[WorldExpansionAlternative] = Field(min_length=2, max_length=5)


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
    ]
  }
}
至少给出两个真正不同的替代方案。不得声称候选已经成为事实，不得编造 NPC、PC、地图、
棋子或模组实体 ID，不得提前揭示未解锁剧透。若候选可能改变剧情锚点，必须使用
anchor_bridge 并在 conflicts 或 assumptions 中说明风险。""".strip()


def parse_world_expansion_output(raw: str) -> WorldExpansionOutput:
    try:
        payload = json.loads(_extract_json(raw))
        return WorldExpansionOutput.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, StructuredOutputError) as exc:
        raise StructuredOutputError(str(exc)) from exc


__all__ = [
    "WORLD_EXPANSION_OUTPUT_INSTRUCTIONS",
    "WorldExpansionCandidate",
    "WorldExpansionOutput",
    "parse_world_expansion_output",
]
