"""Deterministic transport projections for human-KP help.

The scenario contract remains authoritative.  This module only turns typed
contract data into bounded, display-only JSON for either the model adapter or
the HTTP response.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Sequence
from itertools import chain, islice
from typing import Any

from pydantic import ValidationError

from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    ScenarioContract,
    SkillChoice,
    SourceRef,
    WorldCommand,
)
from ai_kp.platform.resolution.director_brief import (
    DirectorBrief,
    DirectorBriefCandidate,
)
from ai_kp.platform.resolution.json_projection import (
    canonical_json_bytes,
    json_byte_size,
    shorten_json_string,
)

SOURCE_REF_LIMIT = 16
SKILL_CHOICE_LIMIT = 8
ACTION_EFFECT_LIMIT = 32

# The application payload is deliberately smaller than the adapter's 64 KiB
# input ceiling.  The margin covers normalization performed at that boundary.
DIRECTOR_HELP_MODEL_REQUEST_JSON_BYTES = 56 * 1024
_MODEL_CANDIDATE_LIMIT = 4
_MODEL_EVIDENCE_LIMIT = 12
_MODEL_SKILL_CHOICE_LIMIT = 2
_MODEL_ACTION_EFFECT_LIMIT = 4
_MODEL_AUTOMATIC_INFORMATION_LIMIT = 2
_MODEL_SKILL_DETAIL_LIMIT = 2
_MODEL_EVIDENCE_IDS_PER_CANDIDATE = 4
_MODEL_STEP_OPERATOR_LIMIT = 4
_MODEL_STATE_JSON_BYTES = 16 * 1024
_MODEL_CANDIDATE_JSON_BYTES = 8 * 1024
_MODEL_EVIDENCE_JSON_BYTES = 2 * 1024
_MODEL_QUESTION_UTF8_BYTES = 2 * 1024
_MODEL_TITLE_UTF8_BYTES = 256
_MODEL_REASON_UTF8_BYTES = 384
_MODEL_EVIDENCE_TEXT_UTF8_BYTES = 512
_MODEL_LOCATOR_UTF8_BYTES = 256
_MODEL_EFFECT_UTF8_BYTES = 256
_MODEL_DETAIL_UTF8_BYTES = 160

_RESPONSE_EVIDENCE_ID_LIMIT = 320
_RESPONSE_SOURCE_TYPE_LIMIT = 160
_RESPONSE_TITLE_LIMIT = 500
_RESPONSE_LOCATOR_LIMIT = 1000
_RESPONSE_ACTION_REASON_LIMIT = 2000
_RESPONSE_ACTION_DETAIL_LIMIT = 1000
_RESPONSE_EFFECT_UTF8_BYTES = 1000
_COMMAND_JSON_MAX_DEPTH = 3
_COMMAND_JSON_CONTAINER_ITEMS = 8
_COMMAND_JSON_STRING_UTF8_BYTES = 256


class DirectorHelpProjector:
    """Project trusted scenario data into bounded model and response payloads."""

    @classmethod
    def model_request(
        cls,
        *,
        contract: ScenarioContract,
        brief: DirectorBrief,
        evidence: tuple[dict[str, Any], ...],
        evidence_total_count: int | None,
        evidence_total_count_lower_bound: int,
        evidence_truncated: bool,
        basis_hash: str,
    ) -> dict[str, Any]:
        """Return the exact, bounded data plane seen by the help model."""

        source_candidates = brief.candidates[:_MODEL_CANDIDATE_LIMIT]
        model_evidence = cls._model_evidence(
            brief=brief,
            evidence=evidence,
            candidates=source_candidates,
        )
        evidence_ids = {str(item["evidence_id"]) for item in model_evidence}
        operators = {item.operator_id: item for item in contract.operators}
        projected_candidates: list[dict[str, Any]] = []
        for candidate in source_candidates:
            candidate_evidence_ids = [
                evidence_id for evidence_id in candidate.evidence_ids if evidence_id in evidence_ids
            ][:_MODEL_EVIDENCE_IDS_PER_CANDIDATE]
            if not candidate_evidence_ids:
                continue
            projected = cls._model_candidate(
                candidate,
                operators.get(candidate.candidate_id),
                evidence_ids=candidate_evidence_ids,
            )
            if _json_bytes(projected) <= _MODEL_CANDIDATE_JSON_BYTES:
                projected_candidates.append(projected)

        state = cls._model_state(brief)

        def assemble() -> dict[str, Any]:
            offered_evidence_ids = {str(item["evidence_id"]) for item in model_evidence}
            candidates = []
            for candidate in projected_candidates:
                retained_evidence_ids = [
                    evidence_id
                    for evidence_id in candidate["evidence_ids"]
                    if evidence_id in offered_evidence_ids
                ]
                if not retained_evidence_ids:
                    continue
                candidates.append(
                    {
                        **candidate,
                        "evidence_ids": retained_evidence_ids,
                        "evidence_ids_truncated": (
                            candidate["evidence_ids_truncated"]
                            or len(retained_evidence_ids) < candidate["evidence_ids_total_count"]
                        ),
                    }
                )
            candidate_skills = {
                str(candidate["candidate_id"]): [
                    str(choice["skill_key"]) for choice in candidate["skill_choices"]
                ]
                for candidate in candidates
            }
            projected_brief = {
                "basis_hash": basis_hash,
                "contract_hash": brief.contract_hash,
                "question": _bounded_utf8_text(
                    brief.question,
                    _MODEL_QUESTION_UTF8_BYTES,
                ),
                "state": state,
                "candidates": candidates,
                "candidates_total_count": len(brief.candidates),
                "candidates_truncated": len(candidates) < len(brief.candidates),
                "evidence_included_count": len(model_evidence),
                "evidence_available_count": len(evidence),
                "evidence_total_count": evidence_total_count,
                "evidence_total_count_lower_bound": evidence_total_count_lower_bound,
                "evidence_truncated": evidence_truncated or len(model_evidence) < len(evidence),
            }
            return {
                "director_brief": projected_brief,
                "evidence": list(model_evidence),
                "offered_candidate_ids": list(candidate_skills),
                "offered_candidate_skills": candidate_skills,
                "offered_skill_keys": list(
                    dict.fromkeys(
                        skill_key for skills in candidate_skills.values() for skill_key in skills
                    )
                ),
                "offered_evidence_ids": [str(item["evidence_id"]) for item in model_evidence],
            }

        request = assemble()
        # This is a future-proof fence around all component budgets.  Evidence
        # is priority ordered, so removing from the tail is deterministic.
        while _json_bytes(request) > DIRECTOR_HELP_MODEL_REQUEST_JSON_BYTES and model_evidence:
            model_evidence.pop()
            request = assemble()
        if _json_bytes(request) > DIRECTOR_HELP_MODEL_REQUEST_JSON_BYTES:
            raise ValueError("director help model projection exceeded its JSON budget")
        return request

    @classmethod
    def action(
        cls,
        *,
        contract: ScenarioContract,
        selected: DirectorBriefCandidate,
        requested_skill_key: str | None,
    ) -> dict[str, Any]:
        """Build the richer, still bounded action shown to the human KP."""

        if selected.kind == "task_method":
            return {
                "candidate_id": selected.candidate_id,
                "kind": selected.kind,
                "title": _response_text(selected.title, 240, fallback="Scenario action"),
                "available": selected.available,
                "reason": _response_text(selected.reason, _RESPONSE_ACTION_REASON_LIMIT),
                "policy": None,
                "selected_skill_key": None,
                "skill_choices": [],
                "skill_choices_total_count": 0,
                "skill_choices_truncated": False,
                "automatic_information": [],
                "maximum_effect": "",
                "step_operator_ids": list(islice(selected.step_operator_ids, 8)),
                "success_effects": [],
                "success_effects_total_count": 0,
                "success_effects_truncated": False,
                "failure_effects": [],
                "failure_effects_total_count": 0,
                "failure_effects_truncated": False,
            }

        operator = next(
            item for item in contract.operators if item.operator_id == selected.candidate_id
        )
        skill_choices = list(islice(operator.skill_choices, SKILL_CHOICE_LIMIT))
        success_effects, success_total = _bounded_command_summaries(
            chain(operator.always_commands, operator.success_commands),
            total_count=len(operator.always_commands) + len(operator.success_commands),
            limit=ACTION_EFFECT_LIMIT,
            text_bytes=_RESPONSE_EFFECT_UTF8_BYTES,
        )
        failure_effects, failure_total = _bounded_command_summaries(
            chain(operator.always_commands, operator.failure_commands),
            total_count=len(operator.always_commands) + len(operator.failure_commands),
            limit=ACTION_EFFECT_LIMIT,
            text_bytes=_RESPONSE_EFFECT_UTF8_BYTES,
        )
        return {
            "candidate_id": selected.candidate_id,
            "kind": selected.kind,
            "title": _response_text(selected.title, 240, fallback="Scenario action"),
            "available": selected.available,
            "reason": _response_text(selected.reason, _RESPONSE_ACTION_REASON_LIMIT),
            "policy": operator.policy,
            "selected_skill_key": requested_skill_key,
            "skill_choices": [_response_skill(choice) for choice in skill_choices],
            "skill_choices_total_count": len(operator.skill_choices),
            "skill_choices_truncated": len(operator.skill_choices) > len(skill_choices),
            "automatic_information": [
                _response_text(item, _RESPONSE_ACTION_DETAIL_LIMIT)
                for item in islice(operator.automatic_information, 12)
            ],
            "maximum_effect": _response_text(
                operator.maximum_effect, _RESPONSE_ACTION_DETAIL_LIMIT
            ),
            "step_operator_ids": [],
            "success_effects": success_effects,
            "success_effects_total_count": success_total,
            "success_effects_truncated": success_total > len(success_effects),
            "failure_effects": failure_effects,
            "failure_effects_total_count": failure_total,
            "failure_effects_truncated": failure_total > len(failure_effects),
        }

    @staticmethod
    def citation_text(
        value: Any,
        max_characters: int,
        *,
        fallback: str = "",
    ) -> str:
        return _response_text(value, max_characters, fallback=fallback)

    @staticmethod
    def citation_locator(value: Any) -> str | None:
        return _response_optional_text(value, _RESPONSE_LOCATOR_LIMIT)

    @staticmethod
    def evidence_id(value: Any) -> str:
        text = _safe_text(value).strip() or "unknown"
        if len(text) <= _RESPONSE_EVIDENCE_ID_LIMIT:
            return text
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        suffix = f"~{digest}"
        return text[: _RESPONSE_EVIDENCE_ID_LIMIT - len(suffix)] + suffix

    @staticmethod
    def source_type(value: Any) -> str:
        return _response_text(
            value,
            _RESPONSE_SOURCE_TYPE_LIMIT,
            fallback="unknown",
        )

    @staticmethod
    def external_source_refs(
        *,
        source_block_id: Any,
        document_id: Any,
        page: Any = None,
    ) -> tuple[list[dict[str, Any]], int, bool]:
        """Represent one external reference, or explicitly report its omission."""

        try:
            source_ref = SourceRef.model_validate(
                {
                    "source_block_id": _safe_text(source_block_id),
                    "document_id": _safe_text(document_id),
                    "page": page,
                    "paragraph": None,
                }
            )
        except (ValidationError, TypeError, ValueError):
            return [], 1, True
        return [source_ref.model_dump(mode="json")], 1, False

    @staticmethod
    def source_locator(source_refs: Sequence[SourceRef]) -> str | None:
        if not source_refs:
            return None
        locators: list[str] = []
        for source in source_refs:
            locator = source.document_id
            if source.page is not None:
                locator += f" · page {source.page}"
            if source.paragraph is not None:
                locator += f" · paragraph {source.paragraph}"
            locators.append(locator)
        return _response_optional_text(
            "; ".join(dict.fromkeys(locators)),
            _RESPONSE_LOCATOR_LIMIT,
        )

    @classmethod
    def _model_evidence(
        cls,
        *,
        brief: DirectorBrief,
        evidence: tuple[dict[str, Any], ...],
        candidates: tuple[DirectorBriefCandidate, ...],
    ) -> list[dict[str, Any]]:
        candidate_evidence_ids = {
            evidence_id for candidate in candidates for evidence_id in candidate.evidence_ids
        }
        location_evidence_ids = {
            item.evidence_id for item in brief.evidence if item.kind == "location"
        }
        ordered = [
            *(
                item
                for item in evidence
                if str(item["evidence_id"]) in candidate_evidence_ids
                or str(item["evidence_id"]) in location_evidence_ids
            ),
            *(item for item in evidence if item["authority"] == "source_context_only"),
            *evidence,
        ]
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in ordered:
            evidence_id = str(item["evidence_id"])
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            source_ref_total = int(item["source_refs_total_count"])
            projected = {
                "evidence_id": evidence_id,
                "source_type": _bounded_utf8_text(item["source_type"], _MODEL_DETAIL_UTF8_BYTES),
                "authority": item["authority"],
                "visibility": item["visibility"],
                "title": _bounded_utf8_text(item["title"], _MODEL_TITLE_UTF8_BYTES),
                "text": _bounded_utf8_text(item["text"], _MODEL_EVIDENCE_TEXT_UTF8_BYTES),
                "source_locator": _bounded_optional_utf8_text(
                    item.get("source_locator"), _MODEL_LOCATOR_UTF8_BYTES
                ),
                "source_refs_total_count": source_ref_total,
                "source_refs_included_count": 0,
                "source_refs_truncated": source_ref_total > 0,
            }
            if _json_bytes(projected) > _MODEL_EVIDENCE_JSON_BYTES:
                projected["title"] = _bounded_utf8_text(item["title"], _MODEL_DETAIL_UTF8_BYTES)
                projected["text"] = _bounded_utf8_text(item["text"], _MODEL_DETAIL_UTF8_BYTES)
                projected["source_locator"] = _bounded_optional_utf8_text(
                    item.get("source_locator"), _MODEL_DETAIL_UTF8_BYTES
                )
            if _json_bytes(projected) > _MODEL_EVIDENCE_JSON_BYTES:
                continue
            selected.append(projected)
            if len(selected) >= _MODEL_EVIDENCE_LIMIT:
                break
        return selected

    @classmethod
    def _model_candidate(
        cls,
        candidate: DirectorBriefCandidate,
        operator: ActionOperator | None,
        *,
        evidence_ids: list[str],
    ) -> dict[str, Any]:
        common = {
            "candidate_id": candidate.candidate_id,
            "kind": candidate.kind,
            "title": _bounded_utf8_text(candidate.title, _MODEL_TITLE_UTF8_BYTES),
            "score": candidate.score,
            "available": candidate.available,
            "reason": _bounded_utf8_text(candidate.reason, _MODEL_REASON_UTF8_BYTES),
            "policy": candidate.policy,
            "evidence_ids": evidence_ids,
            "evidence_ids_total_count": len(candidate.evidence_ids),
            "evidence_ids_truncated": len(evidence_ids) < len(candidate.evidence_ids),
        }
        if candidate.kind == "task_method":
            step_ids = list(islice(candidate.step_operator_ids, _MODEL_STEP_OPERATOR_LIMIT))
            return {
                **common,
                "skill_choices": [],
                "skill_choices_total_count": 0,
                "skill_choices_truncated": False,
                "automatic_information": [],
                "automatic_information_total_count": 0,
                "automatic_information_truncated": False,
                "maximum_effect": "",
                "step_operator_ids": step_ids,
                "step_operator_ids_total_count": len(candidate.step_operator_ids),
                "step_operator_ids_truncated": len(candidate.step_operator_ids) > len(step_ids),
                "success_effects": [],
                "success_effects_total_count": 0,
                "success_effects_truncated": False,
                "failure_effects": [],
                "failure_effects_total_count": 0,
                "failure_effects_truncated": False,
            }

        if operator is None:
            return {**common, "evidence_ids": []}
        skills = list(islice(operator.skill_choices, _MODEL_SKILL_CHOICE_LIMIT))
        automatic = [
            _bounded_utf8_text(item, _MODEL_DETAIL_UTF8_BYTES)
            for item in islice(
                operator.automatic_information,
                _MODEL_AUTOMATIC_INFORMATION_LIMIT,
            )
        ]
        success_effects, success_total = _bounded_command_summaries(
            chain(operator.always_commands, operator.success_commands),
            total_count=len(operator.always_commands) + len(operator.success_commands),
            limit=_MODEL_ACTION_EFFECT_LIMIT,
            text_bytes=_MODEL_EFFECT_UTF8_BYTES,
        )
        failure_effects, failure_total = _bounded_command_summaries(
            chain(operator.always_commands, operator.failure_commands),
            total_count=len(operator.always_commands) + len(operator.failure_commands),
            limit=_MODEL_ACTION_EFFECT_LIMIT,
            text_bytes=_MODEL_EFFECT_UTF8_BYTES,
        )
        return {
            **common,
            "skill_choices": [_model_skill(choice) for choice in skills],
            "skill_choices_total_count": len(operator.skill_choices),
            "skill_choices_truncated": len(operator.skill_choices) > len(skills),
            "automatic_information": automatic,
            "automatic_information_total_count": len(operator.automatic_information),
            "automatic_information_truncated": len(operator.automatic_information) > len(automatic),
            "maximum_effect": _bounded_utf8_text(operator.maximum_effect, _MODEL_REASON_UTF8_BYTES),
            "step_operator_ids": [],
            "step_operator_ids_total_count": 0,
            "step_operator_ids_truncated": False,
            "success_effects": success_effects,
            "success_effects_total_count": success_total,
            "success_effects_truncated": success_total > len(success_effects),
            "failure_effects": failure_effects,
            "failure_effects_total_count": failure_total,
            "failure_effects_truncated": failure_total > len(failure_effects),
        }

    @classmethod
    def _model_state(cls, brief: DirectorBrief) -> dict[str, Any]:
        raw = brief.state.model_dump(mode="json")
        collection_budgets = {
            "facts": 2500,
            "entities": 2500,
            "actor_locations": 1000,
            "resources": 1000,
            "clocks": 1000,
            "recent_events": 2500,
        }
        state = {
            "run_id": _bounded_utf8_text(raw["run_id"], _MODEL_TITLE_UTF8_BYTES),
            "contract_id": _bounded_utf8_text(raw["contract_id"], _MODEL_TITLE_UTF8_BYTES),
            "scenario_version": raw["scenario_version"],
            "state_version": raw["state_version"],
            "status": raw["status"],
            "scene_id": _bounded_optional_utf8_text(raw.get("scene_id"), _MODEL_TITLE_UTF8_BYTES),
            "scene_title": _bounded_optional_utf8_text(
                raw.get("scene_title"), _MODEL_TITLE_UTF8_BYTES
            ),
            "ending_id": _bounded_optional_utf8_text(raw.get("ending_id"), _MODEL_TITLE_UTF8_BYTES),
        }
        for field, budget in collection_budgets.items():
            source = raw[field]
            projected = _bounded_json_collection(source, budget)
            state[field] = projected
            total = int(raw[f"{field}_total_count"])
            state[f"{field}_total_count"] = total
            state[f"{field}_truncated"] = bool(raw[f"{field}_truncated"]) or (
                len(projected) < total
            )

        if _json_bytes(state) > _MODEL_STATE_JSON_BYTES:
            for field in reversed(tuple(collection_budgets)):
                state[field] = {} if isinstance(state[field], dict) else []
                state[f"{field}_truncated"] = (
                    state[f"{field}_truncated"] or state[f"{field}_total_count"] > 0
                )
                if _json_bytes(state) <= _MODEL_STATE_JSON_BYTES:
                    break
        if _json_bytes(state) > _MODEL_STATE_JSON_BYTES:
            raise ValueError("director help state projection exceeded its JSON budget")
        return state


def _response_skill(choice: SkillChoice) -> dict[str, Any]:
    return {
        "skill_key": choice.skill_key,
        "difficulty": choice.difficulty,
        "reason": _response_text(choice.reason, 500, fallback="Check"),
        "hidden": choice.hidden,
        "bonus_dice": choice.bonus_dice,
        "allow_push": choice.allow_push,
        "scope": _response_text(choice.scope, 500),
        "supporting_factors": [
            _response_text(item, _RESPONSE_ACTION_DETAIL_LIMIT)
            for item in choice.supporting_factors
        ],
        "automatic_information": [
            _response_text(item, _RESPONSE_ACTION_DETAIL_LIMIT)
            for item in choice.automatic_information
        ],
        "failure_stakes": _response_text(choice.failure_stakes, _RESPONSE_ACTION_DETAIL_LIMIT),
        "pushed_failure_stakes": _response_text(
            choice.pushed_failure_stakes, _RESPONSE_ACTION_DETAIL_LIMIT
        ),
    }


def _model_skill(choice: SkillChoice) -> dict[str, Any]:
    supporting = [
        _bounded_utf8_text(item, _MODEL_DETAIL_UTF8_BYTES)
        for item in islice(choice.supporting_factors, _MODEL_SKILL_DETAIL_LIMIT)
    ]
    automatic = [
        _bounded_utf8_text(item, _MODEL_DETAIL_UTF8_BYTES)
        for item in islice(choice.automatic_information, _MODEL_SKILL_DETAIL_LIMIT)
    ]
    return {
        "skill_key": choice.skill_key,
        "difficulty": choice.difficulty,
        "reason": _bounded_utf8_text(choice.reason, _MODEL_REASON_UTF8_BYTES),
        "hidden": choice.hidden,
        "bonus_dice": choice.bonus_dice,
        "allow_push": choice.allow_push,
        "scope": _bounded_utf8_text(choice.scope, _MODEL_DETAIL_UTF8_BYTES),
        "supporting_factors": supporting,
        "supporting_factors_total_count": len(choice.supporting_factors),
        "supporting_factors_truncated": len(choice.supporting_factors) > len(supporting),
        "automatic_information": automatic,
        "automatic_information_total_count": len(choice.automatic_information),
        "automatic_information_truncated": len(choice.automatic_information) > len(automatic),
        "failure_stakes": _bounded_utf8_text(choice.failure_stakes, _MODEL_REASON_UTF8_BYTES),
        "pushed_failure_stakes": _bounded_utf8_text(
            choice.pushed_failure_stakes, _MODEL_REASON_UTF8_BYTES
        ),
    }


def _bounded_command_summaries(
    commands: Iterable[WorldCommand],
    *,
    total_count: int,
    limit: int,
    text_bytes: int,
) -> tuple[list[str], int]:
    return (
        [
            _bounded_utf8_text(_command_summary(command), text_bytes)
            for command in islice(commands, limit)
        ],
        total_count,
    )


def _command_summary(command: WorldCommand) -> str:
    def value() -> str:
        return _command_json(command.value)

    if command.kind == "set_fact":
        summary = f"设置事实 {command.path} = {value()}"
    elif command.kind == "remove_fact":
        summary = f"移除事实 {command.path}"
    elif command.kind == "set_entity_status":
        summary = f"将 {command.entity_id} 状态设为 {value()}"
    elif command.kind == "update_entity_runtime":
        summary = f"更新 {command.entity_id} 的运行状态：{_command_json(command.payload)}"
    elif command.kind == "move_actor":
        summary = f"将 {command.actor_id} 移动到 {value()}"
    elif command.kind == "adjust_resource":
        summary = f"资源 {command.path} 调整 {command.delta}"
    elif command.kind == "advance_clock":
        summary = f"时钟 {command.clock_id} 推进 {command.delta}"
    elif command.kind == "set_scene":
        summary = f"切换场景到 {value()}"
    elif command.kind == "complete_run":
        summary = f"以结局 {value()} 完成本次模组"
    elif command.kind == "emit_event":
        summary = f"记录事件 {command.event_type}：{_command_json(command.payload)}"
    elif command.kind == "apply_ruleset_effect":
        summary = f"应用规则效果 {command.event_type}：{_command_json(command.payload)}"
    elif command.kind == "activate_contract_overlay":
        summary = "激活已批准的契约扩展"
    elif command.kind == "register_entity":
        summary = f"登记实体 {command.entity_id}"
    elif command.kind == "register_clock":
        summary = f"登记时钟 {command.clock_id} = {value()}"
    else:
        summary = f"登记资源 {command.path} = {value()}"
    return _bounded_utf8_text(summary, _RESPONSE_EFFECT_UTF8_BYTES)


def _command_json(value: Any) -> str:
    projected, truncated = _bounded_command_value(value)
    if truncated:
        projected = {"value": projected, "truncated": True}
    return canonical_json_bytes(projected).decode("utf-8")


def _bounded_command_value(value: Any, *, depth: int = 0) -> tuple[Any, bool]:
    if isinstance(value, str):
        projected = _bounded_utf8_text(value, _COMMAND_JSON_STRING_UTF8_BYTES)
        return projected, projected != _safe_text(value)
    if value is None or isinstance(value, bool):
        return value, False
    if isinstance(value, float):
        return (value, False) if math.isfinite(value) else ("<non-finite number omitted>", True)
    if isinstance(value, int):
        if value.bit_length() > 1024:
            return "<large integer omitted>", True
        return value, False
    if isinstance(value, dict):
        if depth >= _COMMAND_JSON_MAX_DEPTH:
            return {}, bool(value)
        selected: dict[str, Any] = {}
        truncated = len(value) > _COMMAND_JSON_CONTAINER_ITEMS
        for raw_key, child in islice(value.items(), _COMMAND_JSON_CONTAINER_ITEMS):
            key = _bounded_utf8_text(raw_key, _COMMAND_JSON_STRING_UTF8_BYTES)
            if key in selected:
                truncated = True
                continue
            projected, child_truncated = _bounded_command_value(child, depth=depth + 1)
            selected[key] = projected
            truncated = truncated or child_truncated
        return selected, truncated
    if isinstance(value, (list, tuple)):
        if depth >= _COMMAND_JSON_MAX_DEPTH:
            return [], bool(value)
        selected_list: list[Any] = []
        truncated = len(value) > _COMMAND_JSON_CONTAINER_ITEMS
        for child in islice(value, _COMMAND_JSON_CONTAINER_ITEMS):
            projected, child_truncated = _bounded_command_value(child, depth=depth + 1)
            selected_list.append(projected)
            truncated = truncated or child_truncated
        return selected_list, truncated
    return f"<{type(value).__name__} omitted>", True


def _bounded_json_collection(
    source: dict[str, Any] | list[Any],
    byte_budget: int,
) -> dict[str, Any] | list[Any]:
    if isinstance(source, dict):
        selected: dict[str, Any] = {}
        for key, value in source.items():
            proposed = {**selected, str(key): value}
            if _json_bytes(proposed) <= byte_budget:
                selected[str(key)] = value
        return selected
    selected_list: list[Any] = []
    for value in source:
        proposed = [*selected_list, value]
        if _json_bytes(proposed) <= byte_budget:
            selected_list.append(value)
    return selected_list


def _json_bytes(value: Any) -> int:
    return json_byte_size(value)


def _safe_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.encode("utf-8", errors="replace").decode("utf-8")


def _bounded_utf8_text(value: Any, byte_limit: int) -> str:
    text = _safe_text(value)
    return shorten_json_string(text, json_budget=byte_limit)[0]


def _bounded_optional_utf8_text(value: Any, byte_limit: int) -> str | None:
    if value is None:
        return None
    return _bounded_utf8_text(value, byte_limit)


def _response_text(
    value: Any,
    character_limit: int,
    *,
    fallback: str = "",
) -> str:
    text = _safe_text(value)
    if not text.strip():
        text = fallback
    if len(text) <= character_limit:
        return text
    return text[: character_limit - 1] + "…"


def _response_optional_text(value: Any, character_limit: int) -> str | None:
    if value is None:
        return None
    return _response_text(value, character_limit)


__all__ = [
    "ACTION_EFFECT_LIMIT",
    "DIRECTOR_HELP_MODEL_REQUEST_JSON_BYTES",
    "SKILL_CHOICE_LIMIT",
    "SOURCE_REF_LIMIT",
    "DirectorHelpProjector",
]
