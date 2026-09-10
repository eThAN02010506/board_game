"""Deterministic boundary for explicit selection of a published operator."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from ai_kp.platform.resolution.candidate_ranking import SemanticCandidateRanker
from ai_kp.platform.resolution.contracts import ScenarioContract, ScenarioSnapshot
from ai_kp.platform.resolution.semantic_adapter import (
    SemanticCandidate,
    SemanticSelection,
    SemanticSelectionResult,
)
from ai_kp.platform.resolution.tabletop_turn import (
    TabletopTurnFrame,
    TabletopTurnInterpretation,
)

ExplicitSelectionStatus = Literal[
    "selected", "invalid_syntax", "unknown", "ambiguous", "unavailable"
]

_POSITIVE_FORMS = (
    re.compile(r"^\s*选择已发布行动\s*“(?P<label>[^“”]+)”\s*[。.!！]?\s*$"),
    re.compile(r'^\s*选择已发布行动\s*"(?P<label>[^"\r\n]+)"\s*[。.!！]?\s*$'),
    re.compile(r"^\s*选择已发布行动\s*「(?P<label>[^「」]+)」\s*[。.!！]?\s*$"),
    re.compile(r"^\s*选择已发布行动\s*『(?P<label>[^『』]+)』\s*[。.!！]?\s*$"),
    re.compile(
        r'^\s*select\s+published\s+action\s*"(?P<label>[^"\r\n]+)"'
        r"\s*[.!]?\s*$",
        re.IGNORECASE,
    ),
)
_EXPLICIT_MARKER = re.compile(
    r"选择已发布行动|select\s+published\s+action",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExplicitOperatorResolution:
    """One parsed command and its server-owned authority result."""

    status: ExplicitSelectionStatus
    label: str
    interpretation: TabletopTurnInterpretation
    selection_result: SemanticSelectionResult | None
    reason: str


def resolve_explicit_published_operator(
    contract: ScenarioContract,
    snapshot: ScenarioSnapshot,
    player_action: str,
) -> ExplicitOperatorResolution | None:
    """Resolve only the closed, whole-message published-action syntax.

    Ordinary prose returns ``None`` and remains available to the semantic Agent.
    Any message containing the reserved marker but not matching the complete
    positive form is rejected deterministically, so negation and casual mention
    cannot accidentally acquire operator authority.
    """

    match = None
    for pattern in _POSITIVE_FORMS:
        match = pattern.fullmatch(player_action)
        if match is not None:
            break
    if match is None:
        if not _EXPLICIT_MARKER.search(player_action):
            return None
        return _rejected(
            player_action,
            status="invalid_syntax",
            label="",
            reason=(
                '显式算子选择必须完整使用：选择已发布行动“行动标题”。'
                "否定、转述或附加叙述不会授予算子权限。"
            ),
        )

    label = match.group("label").strip()
    if not label:
        return _rejected(
            player_action,
            status="invalid_syntax",
            label="",
            reason="请在显式选择语法中填写已发布行动的完整标题。",
        )

    ranked = SemanticCandidateRanker().rank(
        contract,
        label,
        snapshot=snapshot,
        include_task_methods=False,
        limit=max(1, len(contract.operators)),
    )
    normalized_label = _normalized_label(label)
    matches = tuple(
        candidate
        for candidate in ranked
        if candidate.kind == "operator"
        and _normalized_label(candidate.title) == normalized_label
    )
    if not matches:
        return _rejected(
            player_action,
            status="unknown",
            label=label,
            reason=f'已发布契约中没有标题为“{label}”的行动；本次没有执行或写入状态。',
        )
    if len(matches) != 1:
        return _rejected(
            player_action,
            status="ambiguous",
            label=label,
            reason=f'标题“{label}”对应多个已发布行动；请由 KP 先消除契约中的标题歧义。',
        )

    candidate = matches[0]
    allowed_skills = candidate.allowed_skill_keys
    selection_result = SemanticSelectionResult(
        selection=SemanticSelection(
            kind="operator",
            candidate_id=candidate.candidate_id,
            requested_skill_key=None,
            confidence="high",
        ),
        offered_candidates=(
            SemanticCandidate(
                candidate_id=candidate.candidate_id,
                kind="operator",
                title=candidate.title,
                intent_hints=candidate.intent_hints,
                allowed_skill_keys=allowed_skills,
                available=candidate.available,
            ),
        ),
        allowed_skill_keys=allowed_skills,
        requires_manual_confirmation=True,
        attempt_count=0,
    )
    if not candidate.available:
        return ExplicitOperatorResolution(
            status="unavailable",
            label=label,
            interpretation=_interpretation(player_action, mechanical=True),
            selection_result=selection_result,
            reason=(
                f'行动“{label}”存在，但在当前冻结场景状态中不可用；'
                "规则内核不会生成可执行预览。"
            ),
        )
    return ExplicitOperatorResolution(
        status="selected",
        label=label,
        interpretation=_interpretation(player_action, mechanical=True),
        selection_result=selection_result,
        reason="已按玩家显式语法选中唯一的已发布行动。",
    )


def _rejected(
    player_action: str,
    *,
    status: Literal["invalid_syntax", "unknown", "ambiguous"],
    label: str,
    reason: str,
) -> ExplicitOperatorResolution:
    return ExplicitOperatorResolution(
        status=status,
        label=label,
        interpretation=_interpretation(player_action, mechanical=False),
        selection_result=None,
        reason=reason,
    )


def _interpretation(
    player_action: str, *, mechanical: bool
) -> TabletopTurnInterpretation:
    return TabletopTurnInterpretation(
        frame=TabletopTurnFrame(
            kind="action",
            goal=player_action[:1000],
            method="explicit_published_operator_selection",
            confidence="high",
        ),
        route="mechanical" if mechanical else "clarification",
        attempt_count=0,
    )


def _normalized_label(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


__all__ = ["ExplicitOperatorResolution", "resolve_explicit_published_operator"]
