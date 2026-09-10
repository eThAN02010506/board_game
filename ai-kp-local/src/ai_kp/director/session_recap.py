"""Strict model contract and bounded prompt for session recap candidates."""

import json

from pydantic import Field, ValidationError, model_validator

from ai_kp.director.context_builder import ContextAssembly, estimate_tokens
from ai_kp.director.turn_output import (
    MemoryScope,
    StrictModel,
    StructuredOutputError,
    Visibility,
)
from ai_kp.platform.structured_json import decode_json_object

PROMPT_VERSION = "session-recap.v1"
MAX_RECAP_EVENTS = 250
MAX_RECAP_EVENT_CHARACTERS = 60_000


class SessionRecapCandidateOutput(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    scope: MemoryScope
    importance: int = Field(ge=1, le=5)
    visibility: Visibility
    pc_id: str | None = None
    npc_id: str | None = None
    happened_at: str | None = None
    source_event_ids: list[str] = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def player_memory_requires_pc(self) -> "SessionRecapCandidateOutput":
        if self.visibility == "player" and not self.pc_id:
            raise ValueError("player-visible recap memory requires pc_id")
        if len(set(self.source_event_ids)) != len(self.source_event_ids):
            raise ValueError("source_event_ids must not contain duplicates")
        return self


class SessionRecapOutput(StrictModel):
    candidates: list[SessionRecapCandidateOutput] = Field(max_length=30)


SESSION_RECAP_INSTRUCTIONS = """你是跑团团次记录整理器，不是故事续写器。
只根据输入的冻结事件窗口，提出值得长期保存的记忆候选。不要补写未发生事实。
只返回一个 JSON 对象，不要 Markdown 或解释：
{
  "candidates": [{
    "text": "简洁、客观、可独立理解的记忆",
    "scope": "campaign_fact|pc_major|pc_side|npc_interaction|npc_relationship|location_fact|clue",
    "importance": 1,
    "visibility": "player|table|kp",
    "pc_id": null,
    "npc_id": null,
    "happened_at": null,
    "source_event_ids": ["evt_..."],
    "rationale": "仅 KP 可见：为何值得长期保存"
  }]
}
规则：
1. 每项至少引用一个输入中真实存在的 event id，不得编造任何 ID。
2. player 可见候选必须填写对应 pc_id；面向全桌的共同记忆使用 table。
3. 任何引用 kp 可见事件的候选必须为 kp；不得降低保密级别。
4. 不重复“已有记忆”，不把普通寒暄、重复叙述或纯系统操作写成长久记忆。
5. 优先整理主要事件、支线进展、关键线索、重要 NPC 共同经历和关系变化。
6. 没有值得保存的内容时返回 {"candidates":[]}。
""".strip()


def parse_session_recap_output(raw: str) -> SessionRecapOutput:
    try:
        return SessionRecapOutput.model_validate(decode_json_object(raw))
    except (ValidationError, ValueError) as exc:
        raise StructuredOutputError(str(exc)) from exc


def build_session_recap_context(
    snapshot: dict,
    *,
    skill_instructions: str = "",
) -> ContextAssembly:
    content = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    system_content = SESSION_RECAP_INSTRUCTIONS
    if skill_instructions:
        system_content = f"{system_content}\n\n{skill_instructions}"
    messages = [
        {
            "role": "system",
            "content": system_content,
        },
        {
            "role": "user",
            "content": (
                "以下是只读的团次事件窗口、可用角色/NPC ID 与已有记忆。"
                "输出候选，不执行写入。\n" + content
            ),
        },
    ]
    return ContextAssembly(
        messages=messages,
        included_sources=[
            {
                "kind": "session_event_window",
                "id": snapshot["event_window_hash"],
                "label": snapshot["session"]["title"],
                "visibility": "kp",
                "required": True,
            }
        ],
        excluded_sources=[],
        token_estimate=sum(estimate_tokens(item["content"]) for item in messages),
        visibility_scope="kp",
    )


__all__ = [
    "MAX_RECAP_EVENTS",
    "MAX_RECAP_EVENT_CHARACTERS",
    "PROMPT_VERSION",
    "SessionRecapCandidateOutput",
    "SessionRecapOutput",
    "build_session_recap_context",
    "parse_session_recap_output",
]
