"""Ruleset-neutral tabletop conversation framing and routing policy."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai_kp.platform.ports.llm import ChatMessage, LlmClient
from ai_kp.platform.resolution.contracts import (
    EntitySpec,
    ScenarioContract,
    ScenarioSnapshot,
)
from ai_kp.platform.structured_json import decode_json_object

TabletopActKind = Literal[
    "out_of_character",
    "world_question",
    "npc_dialogue",
    "action",
    "multi_step_action",
    "time_advance",
    "result_assertion",
]
TabletopRoute = Literal[
    "conversation",
    "information",
    "roleplay",
    "mechanical",
    "clarification",
]

_OPENING_NAME_TOKEN = re.compile(r"[A-Za-zÀ-ÿ][\w'-]{2,}")
_GENERIC_NAME_TOKENS = {
    "house", "room", "street", "city", "mister", "missus", "keeper",
    "location", "building", "person", "unknown",
}
_VISIBLE_WORLD_QUESTION = re.compile(
    r"(?:\bwhat\s+(?:can|do)\s+(?:i|we)\s+(?:see|hear)\b|"
    r"\b(?:can|could)\s+(?:i|we)\s+(?:see|hear)\b|"
    r"(?:我|我们).{0,10}(?:能|可以).{0,6}(?:看见|看到|听见|听到)|"
    r"(?:现在|眼前|周围).{0,8}(?:有什么|能看到|能看见|能听到))",
    re.IGNORECASE,
)
_RESULT_COMPLETION_MARKER = re.compile(
    r"(?:已经|已然|成功地|\balready\b|\bnow\b|\bhas\b|\bhave\b)",
    re.IGNORECASE,
)
_RESULT_STATE_VERB = re.compile(
    r"(?:信任|屈服|投降|交出|说出|告诉|透露|承认|完成|打开|"
    r"\btrust(?:s|ed)?\b|\bsurrender(?:s|ed)?\b|\bhand(?:s|ed)?\s+over\b|"
    r"\breveal(?:s|ed)?\b|\btell(?:s|ing)?\b|\btold\b|\badmit(?:s|ted)?\b)",
    re.IGNORECASE,
)
_EXPLICIT_SOCIAL_ACTION = re.compile(
    r"(?:(?:我|我们).{0,80}(?:说服|劝服|威胁|恐吓|欺骗|哄骗|交涉|谈判)|"
    r"\b(?:i|we)\b.{0,80}\b(?:persuade|convince|intimidate|threaten|deceive|"
    r"negotiate)\b)",
    re.IGNORECASE,
)
_ORDERED_STEP_SEPARATOR = re.compile(
    r"\s*(?:，|,|；|;)?\s*(?:再|然后|接着|随后|之后|\bthen\b|\bnext\b)\s*",
    re.IGNORECASE,
)
_ORDERED_PLAN_MARKER = re.compile(
    r"(?:^|[，,。.!?]\s*)(?:我|我们)先|\b(?:i|we)\s+(?:first|will first)\b",
    re.IGNORECASE,
)


class TabletopModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class MaterialAmbiguity(TabletopModel):
    """One missing choice that would materially change authority, risk, or effect."""

    field: Literal["goal", "method", "target", "scope", "duration"]
    question: str = Field(min_length=1, max_length=800)
    why_material: str = Field(min_length=1, max_length=800)


class TabletopTurnFrame(TabletopModel):
    """Untrusted semantic frame; it grants no permission to mutate world state."""

    kind: TabletopActKind
    goal: str = Field(default="", max_length=1000)
    method: str = Field(default="", max_length=1000)
    target_entity_ids: tuple[str, ...] = Field(default=(), max_length=8)
    dialogue: str = Field(default="", max_length=2000)
    steps: tuple[str, ...] = Field(default=(), max_length=8)
    time_span: str = Field(default="", max_length=200)
    ambiguity: MaterialAmbiguity | None = None
    confidence: Literal["low", "medium", "high"] = "low"

    @model_validator(mode="after")
    def validate_shape(self) -> TabletopTurnFrame:
        if self.kind == "npc_dialogue" and not self.dialogue:
            raise ValueError("NPC dialogue requires the words or gist addressed to the NPC")
        if self.kind == "multi_step_action" and len(self.steps) < 2:
            raise ValueError("A multi-step action requires at least two ordered steps")
        if self.kind == "time_advance" and not self.time_span and self.ambiguity is None:
            raise ValueError("Time advance requires a duration or a material ambiguity")
        if self.kind in {"action", "multi_step_action"} and not self.goal:
            raise ValueError("A mechanical action requires a goal")
        return self


class TabletopTurnInterpretation(TabletopModel):
    frame: TabletopTurnFrame
    route: TabletopRoute
    attempt_count: int = Field(ge=0, le=2)
    audit_count: int = Field(default=0, ge=0, le=1)
    audit_reason: str = Field(default="", max_length=800)
    validation_errors: tuple[str, ...] = ()


class TabletopKindAudit(TabletopModel):
    kind: Literal["world_question", "npc_dialogue", "action", "result_assertion"]
    reason: str = Field(min_length=1, max_length=800)


class TabletopTurnPolicy:
    """Derive routing from the frame and current public identifiers."""

    @staticmethod
    def route(
        frame: TabletopTurnFrame,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot | None = None,
    ) -> TabletopRoute:
        known_entities = {item.entity_id for item in contract.entities}
        known_references = known_entities | {
            item.location_id for item in contract.locations
        }
        if set(frame.target_entity_ids) - known_references:
            return "clarification"
        if frame.ambiguity is not None:
            return "clarification"
        if (
            snapshot is not None
            and frame.kind == "npc_dialogue"
            and not (set(frame.target_entity_ids) & known_entities)
            <= _entities_in_current_scene(contract, snapshot)
        ):
            return "clarification"
        if frame.kind == "out_of_character":
            return "conversation"
        if frame.kind == "world_question":
            return "information"
        if frame.kind == "npc_dialogue":
            return "roleplay"
        if frame.kind == "result_assertion":
            return "clarification"
        return "mechanical"

    @staticmethod
    def clarification(
        frame: TabletopTurnFrame,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot | None = None,
    ) -> str:
        if frame.ambiguity is not None:
            return frame.ambiguity.question
        known_entities = {item.entity_id for item in contract.entities}
        known_references = known_entities | {
            item.location_id for item in contract.locations
        }
        if set(frame.target_entity_ids) - known_references:
            return "你指的是当前场景中的哪个具体对象？"
        if (
            snapshot is not None
            and frame.kind == "npc_dialogue"
            and not (set(frame.target_entity_ids) & known_entities)
            <= _entities_in_current_scene(contract, snapshot)
        ):
            return "当前无法确认你能直接与该对象交谈。请说明见面地点或使用的通信方式。"
        if frame.kind == "result_assertion":
            return "这是你想达成的结果。请说明角色准备采用什么具体做法来达成它？"
        return "请补充会影响裁定的目标、做法或对象。"


def opening_participant_ids(
    contract: ScenarioContract,
    opening_narration: str | None,
) -> tuple[str, ...]:
    """Match only entities explicitly named in source-authorized opening prose."""

    if not opening_narration:
        return ()
    folded_opening = opening_narration.casefold()
    participants: list[str] = []
    for entity in contract.entities:
        title = entity.title.strip()
        exact_named = len(title) >= 2 and title.casefold() in folded_opening
        latin_tokens = {
            token.casefold()
            for token in _OPENING_NAME_TOKEN.findall(title)
            if len(token) >= 4 and token.casefold() not in _GENERIC_NAME_TOKENS
        }
        transliterated_named = any(token in folded_opening for token in latin_tokens)
        if exact_named or transliterated_named:
            participants.append(entity.entity_id)
    return tuple(participants)


class ConstrainedTabletopTurnAdapter:
    """Classify the table contribution before any rules or operator selection."""

    def __init__(self, llm: LlmClient):
        self.llm = llm

    async def interpret(
        self,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_text: str,
    ) -> TabletopTurnInterpretation:
        guarded = self._deterministic_guard(player_text)
        if guarded is not None:
            return TabletopTurnInterpretation(
                frame=guarded,
                route=TabletopTurnPolicy.route(guarded, contract, snapshot),
                attempt_count=0,
            )
        errors: list[str] = []
        for attempt in range(1, 3):
            raw = await self.llm.complete(
                self._messages(contract, snapshot, player_text, errors),
                temperature=0.0,
            )
            try:
                frame = TabletopTurnFrame.model_validate(
                    self._normalize_transport(decode_json_object(raw), contract)
                )
                frame = self._resolve_single_dialogue_target(
                    frame, contract, snapshot
                )
                frame, audit_count, audit_reason = await self._audit_if_ambiguous(
                    contract, snapshot, player_text, frame
                )
                frame = self._resolve_single_dialogue_target(
                    frame, contract, snapshot
                )
                route = TabletopTurnPolicy.route(frame, contract, snapshot)
                return TabletopTurnInterpretation(
                    frame=frame,
                    route=route,
                    attempt_count=attempt,
                    audit_count=audit_count,
                    audit_reason=audit_reason,
                    validation_errors=tuple(errors),
                )
            except (ValidationError, ValueError) as exc:
                errors.append(str(exc)[:600])

        ordered_plan = self._ordered_plan_fallback(player_text)
        if ordered_plan is not None:
            return TabletopTurnInterpretation(
                frame=ordered_plan,
                route="mechanical",
                attempt_count=2,
                validation_errors=tuple(errors),
            )

        # A weak model failing to produce the protocol must never guess which
        # authority path the player intended. Preserve the text as an action-shaped
        # frame for retry/audit, but force a visible clarification before any
        # operator selection or state-changing work can start.
        fallback = TabletopTurnFrame(
            kind="action",
            goal=player_text[:1000],
            method=player_text[:1000],
            ambiguity=MaterialAmbiguity(
                field="method",
                question=(
                    "我暂时无法可靠理解这次发言。请换一种说法，说明角色想做什么、"
                    "对谁或什么做，以及采用什么方法。"
                ),
                why_material="错误理解可能改变检定、风险或权威状态。",
            ),
            confidence="low",
        )
        return TabletopTurnInterpretation(
            frame=fallback,
            route="clarification",
            attempt_count=2,
            validation_errors=tuple(errors),
        )

    @staticmethod
    def _deterministic_guard(player_text: str) -> TabletopTurnFrame | None:
        """Recognize only high-precision, non-authoritative tabletop acts."""

        normalized = str(player_text or "").strip()
        if not normalized:
            return None
        if _VISIBLE_WORLD_QUESTION.search(normalized):
            return TabletopTurnFrame(
                kind="world_question",
                goal=normalized[:1000],
                method="直接感知当前公开场景",
                confidence="high",
            )
        if (
            _RESULT_COMPLETION_MARKER.search(normalized)
            and _RESULT_STATE_VERB.search(normalized)
        ):
            return TabletopTurnFrame(
                kind="result_assertion",
                goal=normalized[:1000],
                confidence="high",
            )
        if _EXPLICIT_SOCIAL_ACTION.search(normalized):
            return TabletopTurnFrame(
                kind="action",
                goal=normalized[:1000],
                method=normalized[:1000],
                confidence="high",
            )
        return None

    @staticmethod
    def _ordered_plan_fallback(player_text: str) -> TabletopTurnFrame | None:
        """Recover explicit ordered plans after schema failure, never outcomes."""

        normalized = str(player_text or "").strip()
        if not _ORDERED_PLAN_MARKER.search(normalized):
            return None
        steps = tuple(
            part.strip(" ，,；;。.")
            for part in _ORDERED_STEP_SEPARATOR.split(normalized)
            if part.strip(" ，,；;。.")
        )
        if len(steps) < 2:
            return None
        return TabletopTurnFrame(
            kind="multi_step_action",
            goal=normalized[:1000],
            method=normalized[:1000],
            steps=steps[:8],
            confidence="low",
        )

    @staticmethod
    def _resolve_single_dialogue_target(
        frame: TabletopTurnFrame,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
    ) -> TabletopTurnFrame:
        """Resolve ordinary pronouns when exactly one colocated NPC can answer."""

        if frame.kind != "npc_dialogue" or frame.target_entity_ids:
            return frame
        current_npcs = tuple(
            item.entity_id
            for item in contract.entities
            if item.entity_type == "npc"
            and item.entity_id in _entities_in_current_scene(contract, snapshot)
        )
        if len(current_npcs) != 1:
            return frame
        return frame.model_copy(update={"target_entity_ids": current_npcs})

    @staticmethod
    def _normalize_transport(payload: object, contract: ScenarioContract) -> object:
        """Repair representation noise without inventing conversation semantics."""

        if not isinstance(payload, dict):
            return payload
        normalized = dict(payload)
        for field in ("goal", "method", "dialogue", "time_span"):
            value = normalized.get(field)
            if value is None or value == () or value == []:
                normalized[field] = ""
        for field in ("target_entity_ids", "steps"):
            value = normalized.get(field)
            if value is None or value == "":
                normalized[field] = []
            elif field == "steps" and isinstance(value, str):
                normalized[field] = [
                    item.strip(" ，,；;。.")
                    for item in _ORDERED_STEP_SEPARATOR.split(value)
                    if item.strip(" ，,；;。.")
                ]
            elif isinstance(value, list):
                normalized[field] = [item for item in value if isinstance(item, str)]
        targets = normalized.get("target_entity_ids")
        if isinstance(targets, list):
            ids_by_label = {
                item.title.casefold(): item.entity_id for item in contract.entities
            }
            normalized["target_entity_ids"] = [
                ids_by_label.get(item.casefold(), item) for item in targets
            ]
        ambiguity = normalized.get("ambiguity")
        if ambiguity == "" or ambiguity == () or ambiguity == []:
            normalized["ambiguity"] = None
        confidence = normalized.get("confidence")
        if confidence == "":
            normalized["confidence"] = "low"
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            normalized["confidence"] = (
                "high" if confidence >= 0.75 else "medium" if confidence >= 0.4 else "low"
            )
        kind = normalized.get("kind")
        if isinstance(kind, str):
            candidate = kind.casefold().strip().replace("-", "_").replace(" ", "_")
            if candidate in {
                "out_of_character",
                "world_question",
                "npc_dialogue",
                "action",
                "multi_step_action",
                "time_advance",
                "result_assertion",
            }:
                normalized["kind"] = candidate
        return normalized

    async def _audit_if_ambiguous(
        self,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_text: str,
        frame: TabletopTurnFrame,
    ) -> tuple[TabletopTurnFrame, int, str]:
        structural_conflict = (
            frame.kind == "world_question"
            and bool(frame.target_entity_ids or frame.dialogue)
        ) or (frame.kind == "npc_dialogue" and bool(frame.steps)) or (
            frame.kind == "action"
            and bool(frame.dialogue)
            and not frame.steps
        )
        if not structural_conflict:
            return frame, 0, ""
        raw = await self.llm.complete(
            [
                ChatMessage(
                    role="system",
                    content=(
                        "你是独立的桌面对话类型审查器，只返回 JSON，不能选择规则、技能、"
                        "行动 operator 或结果。world_question 是玩家询问角色无需行动即可"
                        "直接感知的世界信息；npc_dialogue 是角色对 NPC 说话并只等待其自然"
                        "回答；action 是角色采用手段，试图改变他人认知、立场、行为或世界；"
                        "result_assertion 是把尚未结算的期望结果说成已经发生。判断说话对象："
                        "角色向 NPC 发问属于 npc_dialogue；玩家向主持人询问 NPC 或场景的"
                        "可见状态属于 world_question。语法上声称 NPC 已信任、已屈服或事件已"
                        "成功，不是问题，而是 result_assertion。例：‘I ask the guard what he "
                        "saw’ 是 npc_dialogue；‘What can I see about the guard?’ 是 "
                        "world_question；‘The guard now trusts me’ 是 result_assertion；"
                        "‘I present evidence to persuade the guard’ 是 action。"
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=(
                        f"玩家原文：{player_text}\n初步 frame：{frame.model_dump_json()}\n"
                        f"当前场景实体：{json.dumps([item.entity_id for item in contract.entities if item.entity_id in _entities_in_current_scene(contract, snapshot)], ensure_ascii=False)}\n"
                        "只返回 {kind,reason}；kind 只能是 world_question、npc_dialogue、"
                        "action、result_assertion。"
                    ),
                ),
            ],
            temperature=0.0,
        )
        try:
            decoded = decode_json_object(raw)
            payload = {
                "kind": decoded.get("kind"),
                "reason": decoded.get("reason") or "独立审查确认会话类型。",
            }
            audit = TabletopKindAudit.model_validate(payload)
            revised = frame.model_copy(update={"kind": audit.kind})
            return TabletopTurnFrame.model_validate(
                revised.model_dump(mode="python")
            ), 1, audit.reason
        except (ValidationError, ValueError):
            return frame, 1, "独立会话类型审查输出无效，保留初步 frame。"

    @staticmethod
    def _messages(
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_text: str,
        errors: list[str],
    ) -> list[ChatMessage]:
        current_locations = _current_locations(snapshot)
        public_catalog = {
            "scene_id": snapshot.scene_id,
            "actor_locations": snapshot.actor_locations,
            "locations": [
                {
                    "id": item.location_id,
                    "title": item.title,
                    "visibility": item.initial_visibility,
                }
                for item in contract.locations
                if item.location_id in current_locations
            ],
            "entities": [
                {
                    "id": item.entity_id,
                    "title": item.title,
                    "type": item.entity_type,
                    "status": snapshot.entities.get(
                        item.entity_id, item.initial_status
                    ),
                    "location_id": _entity_location(item, snapshot),
                }
                for item in contract.entities
                if _entity_location(item, snapshot) in current_locations
            ],
        }
        correction = f"\n上次结构错误：{errors[-1]}" if errors else ""
        return [
            ChatMessage(
                role="system",
                content=(
                    "你只负责判断玩家这句话在桌面角色扮演对话中属于什么，不裁定规则、"
                    "不选择技能、不写结果。先区分桌外交流、询问角色当前可感知的信息、"
                    "单纯对 NPC 说话、声明有目标和做法的行动、有先后依赖的计划、明确"
                    "快进时间，以及把想要结果说成已经成功。普通寒暄或向 NPC 提问属于 "
                    "npc_dialogue；只有明确试图改变其立场、认知或行为时才是 action。"
                    "‘我查看眼前明显有什么’是 world_question；主动搜隐蔽物才是 action。"
                    "只在缺失选择会改变权限、风险、检定对象或成功效果时填写 ambiguity；"
                    "不要追问无关细节。只能引用目录里的实体 id。只返回 JSON。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"玩家原文：{player_text}\n公开目录："
                    f"{json.dumps(public_catalog, ensure_ascii=False)}\n"
                    "返回 {kind,goal,method,target_entity_ids,dialogue,steps,time_span,"
                    "ambiguity,confidence}。kind 只能是 out_of_character、world_question、"
                    "npc_dialogue、action、multi_step_action、time_advance、result_assertion。"
                    "ambiguity 为 null，或 {field,question,why_material}。没有内容的字符串"
                    "和数组分别用空值。" + correction
                ),
            ),
        ]


def _current_locations(snapshot: ScenarioSnapshot) -> set[str]:
    locations = set(snapshot.actor_locations.values())
    if snapshot.scene_id is not None:
        locations.add(snapshot.scene_id)
    return locations


def _entity_location(entity: EntitySpec, snapshot: ScenarioSnapshot) -> str | None:
    runtime = snapshot.entity_runtime.get(entity.entity_id, entity.initial_runtime)
    return runtime.location_id if runtime is not None else entity.initial_location_id


def _entities_in_current_scene(
    contract: ScenarioContract, snapshot: ScenarioSnapshot
) -> set[str]:
    locations = _current_locations(snapshot)
    return {
        item.entity_id
        for item in contract.entities
        if _entity_location(item, snapshot) in locations
    }


__all__ = [
    "ConstrainedTabletopTurnAdapter",
    "MaterialAmbiguity",
    "TabletopActKind",
    "TabletopRoute",
    "TabletopTurnFrame",
    "TabletopTurnInterpretation",
    "TabletopTurnPolicy",
    "opening_participant_ids",
]
