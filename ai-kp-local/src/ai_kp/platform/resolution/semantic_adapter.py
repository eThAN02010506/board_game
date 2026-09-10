"""Constrained semantic adapters shared by small and large language models."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_kp.platform.ports.llm import ChatMessage, LlmClient
from ai_kp.platform.resolution.action_catalog import ScenarioActionCatalogProjector
from ai_kp.platform.resolution.candidate_ranking import (
    LARGE_CANDIDATE_LIMIT,
    SMALL_CANDIDATE_LIMIT,
)
from ai_kp.platform.resolution.contracts import ScenarioContract, ScenarioSnapshot
from ai_kp.platform.structured_json import decode_json_object

AdapterProfile = Literal["small", "large"]
SelectionKind = Literal["operator", "task_method", "clarification"]


class SemanticCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    kind: Literal["operator", "task_method"]
    title: str
    intent_hints: tuple[str, ...] = ()
    allowed_skill_keys: tuple[str, ...] = ()
    available: bool = True


class SemanticSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SelectionKind
    candidate_id: str | None = None
    requested_skill_key: str | None = None
    confidence: Literal["low", "medium", "high"]
    clarification: str | None = Field(default=None, max_length=1000)


class SemanticSelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selection: SemanticSelection
    offered_candidates: tuple[SemanticCandidate, ...]
    allowed_skill_keys: tuple[str, ...]
    requires_manual_confirmation: bool = True
    attempt_count: int = Field(ge=0, le=2)
    validation_errors: tuple[str, ...] = ()


class SemanticCoverageAudit(BaseModel):
    """A deliberately narrow second opinion for weak-model clarification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str | None


class ConstrainedSemanticAdapter:
    """Let a model select IDs; never let it define mechanics or effects."""

    def __init__(self, llm: LlmClient, *, profile: AdapterProfile):
        self.llm = llm
        self.profile = profile

    async def select(
        self,
        contract: ScenarioContract,
        player_intent: str,
        *,
        snapshot: ScenarioSnapshot | None = None,
    ) -> SemanticSelectionResult:
        candidates = self._catalog(contract, player_intent, snapshot=snapshot)
        errors: list[str] = []
        for attempt in range(1, 3):
            raw = await self.llm.complete(
                self._messages(player_intent, candidates, errors), temperature=0.0
            )
            try:
                selection = SemanticSelection.model_validate(
                    self._normalize_representation(decode_json_object(raw))
                )
                allowed_skills = self._validate_selection(selection, candidates)
                if (
                    attempt == 1
                    and self.profile == "small"
                    and candidates
                    and selection.kind == "clarification"
                ):
                    audit_error = (
                        "候选目录非空。独立覆盖审计只可选择已有候选；"
                        "若没有候选完整覆盖则保持 clarification。"
                    )
                    errors.append(audit_error)
                    audited = await self._audit_clarification(
                        player_intent,
                        candidates,
                    )
                    if audited is None:
                        return SemanticSelectionResult(
                            selection=selection,
                            offered_candidates=candidates,
                            allowed_skill_keys=(),
                            attempt_count=2,
                            validation_errors=tuple(errors),
                        )
                    errors.append("独立覆盖审计确认现有候选完整覆盖玩家行动。")
                    selection = SemanticSelection(
                        kind=audited.kind,
                        candidate_id=audited.candidate_id,
                        requested_skill_key=None,
                        confidence="medium",
                    )
                    return SemanticSelectionResult(
                        selection=selection,
                        offered_candidates=candidates,
                        allowed_skill_keys=audited.allowed_skill_keys,
                        attempt_count=2,
                        validation_errors=tuple(errors),
                    )
                return SemanticSelectionResult(
                    selection=selection,
                    offered_candidates=candidates,
                    allowed_skill_keys=allowed_skills,
                    attempt_count=attempt,
                    validation_errors=tuple(errors),
                )
            except (ValidationError, ValueError) as exc:
                errors.append(str(exc)[:500])

        return SemanticSelectionResult(
            selection=SemanticSelection(
                kind="clarification",
                confidence="low",
                clarification="请补充你想达成的目标、使用的手段或交涉内容。",
            ),
            offered_candidates=candidates,
            allowed_skill_keys=(),
            attempt_count=2,
            validation_errors=tuple(errors),
        )

    async def _audit_clarification(
        self,
        player_intent: str,
        candidates: tuple[SemanticCandidate, ...],
    ) -> SemanticCandidate | None:
        """Ask one binary, authority-free coverage question after over-clarification."""

        raw = await self.llm.complete(
            self._coverage_audit_messages(player_intent, candidates),
            temperature=0.0,
        )
        try:
            audit = SemanticCoverageAudit.model_validate(decode_json_object(raw))
        except (ValidationError, ValueError):
            return None
        if audit.candidate_id is None:
            return None
        matches = [
            candidate
            for candidate in candidates
            if candidate.candidate_id == audit.candidate_id and candidate.available
        ]
        return matches[0] if len(matches) == 1 else None

    def _catalog(
        self,
        contract: ScenarioContract,
        player_intent: str,
        *,
        snapshot: ScenarioSnapshot | None,
    ) -> tuple[SemanticCandidate, ...]:
        catalog = ScenarioActionCatalogProjector.project(
            contract,
            snapshot or contract.initial_snapshot("semantic-catalog"),
            player_intent,
            include_task_methods=self.profile == "large",
            limit=(
                LARGE_CANDIDATE_LIMIT
                if self.profile == "large"
                else SMALL_CANDIDATE_LIMIT
            ),
        )
        return tuple(
            SemanticCandidate(
                candidate_id=item.candidate_id,
                kind=item.kind,
                title=item.title,
                intent_hints=item.intent_hints,
                allowed_skill_keys=item.allowed_skill_keys,
                available=item.available,
            )
            for item in catalog.candidates
        )

    @staticmethod
    def _messages(
        player_intent: str,
        candidates: tuple[SemanticCandidate, ...],
        errors: list[str],
    ) -> list[ChatMessage]:
        catalog = [item.model_dump(mode="json") for item in candidates]
        correction = f"\n上次输出错误：{errors[-1]}" if errors else ""
        return [
            ChatMessage(
                role="system",
                content=(
                    "只返回 JSON。只能选择目录中的 candidate_id 和 allowed_skill_keys；"
                    "不能创造规则、效果、技能或结局。confidence 必须是字符串 "
                    "low、medium 或 high；requested_skill_key 必须是单个字符串或 null，"
                    "不能使用数组或数字。available=false 表示当前前置状态不满足；只有玩家"
                    "明确要求该行动时才可选择它，以便规则内核解释缺少的前置条件。"
                    "如果玩家明确描述了多个有先后关系的步骤，而目录中的 task_method "
                    "完整覆盖这些步骤，必须优先选择该 task_method，不能只选其中一个 "
                    "operator。目录非空不代表必须选择，但玩家意图与候选标题或提示明确"
                    "一致时应选择该候选；clarification 只用于会改变规则结果的实质缺失。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"玩家意图：{player_intent}\n候选目录："
                    f"{json.dumps(catalog, ensure_ascii=False)}\n"
                    "返回 {kind:operator|task_method|clarification,candidate_id,"
                    "requested_skill_key,confidence,clarification}。"
                    + correction
                ),
            ),
        ]

    @staticmethod
    def _coverage_audit_messages(
        player_intent: str,
        candidates: tuple[SemanticCandidate, ...],
    ) -> list[ChatMessage]:
        catalog = [
            {
                "candidate_id": item.candidate_id,
                "kind": item.kind,
                "title": item.title,
                "intent_hints": item.intent_hints,
            }
            for item in candidates
            if item.available
        ]
        return [
            ChatMessage(
                role="system",
                content=(
                    "你是独立的候选覆盖审计 Agent。只判断玩家目标和手段是否已被一个"
                    "现有候选完整覆盖；不创造规则、效果、技能或候选。只返回 "
                    '{"candidate_id":"目录中的ID"} 或 {"candidate_id":null}。'
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"玩家行动：{player_intent}\n可用候选："
                    f"{json.dumps(catalog, ensure_ascii=False)}"
                ),
            ),
        ]

    @staticmethod
    def _normalize_representation(payload: Any) -> Any:
        """Repair harmless weak-model type variants before strict authority checks."""

        if not isinstance(payload, dict):
            return payload
        normalized = dict(payload)
        requested = normalized.get("requested_skill_key")
        if requested == []:
            normalized["requested_skill_key"] = None
        elif (
            isinstance(requested, list)
            and len(requested) == 1
            and isinstance(requested[0], str)
        ):
            normalized["requested_skill_key"] = requested[0]

        confidence = normalized.get("confidence")
        if isinstance(confidence, str):
            lowered = confidence.casefold().strip()
            if lowered in {"low", "medium", "high"}:
                normalized["confidence"] = lowered
            else:
                try:
                    confidence = float(lowered)
                except ValueError:
                    confidence = None
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            normalized["confidence"] = (
                "high" if confidence >= 0.75 else "medium" if confidence >= 0.4 else "low"
            )

        if normalized.get("kind") != "clarification" and normalized.get(
            "clarification"
        ) == "":
            normalized["clarification"] = None
        return normalized

    @staticmethod
    def _validate_selection(
        selection: SemanticSelection,
        candidates: tuple[SemanticCandidate, ...],
    ) -> tuple[str, ...]:
        if selection.kind == "clarification":
            if selection.candidate_id is not None or not selection.clarification:
                raise ValueError("Clarification must contain only a concrete question")
            return ()
        match = next(
            (
                item
                for item in candidates
                if item.kind == selection.kind
                and item.candidate_id == selection.candidate_id
            ),
            None,
        )
        if match is None:
            raise ValueError("Model selected a candidate outside the offered catalog")
        if selection.requested_skill_key is not None and (
            selection.requested_skill_key not in match.allowed_skill_keys
        ):
            raise ValueError("Model selected a skill outside the candidate allow-list")
        return match.allowed_skill_keys


__all__ = [
    "ConstrainedSemanticAdapter",
    "SemanticCandidate",
    "SemanticSelection",
    "SemanticSelectionResult",
]
