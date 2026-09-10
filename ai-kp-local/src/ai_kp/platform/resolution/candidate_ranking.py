"""Deterministic candidate retrieval before constrained model selection."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from ai_kp.platform.resolution.causal_validation import unsafe_ending_operator_ids
from ai_kp.platform.resolution.contracts import ScenarioContract, ScenarioSnapshot
from ai_kp.platform.resolution.kernel import ActionResolutionKernel

SMALL_CANDIDATE_LIMIT = 12
LARGE_CANDIDATE_LIMIT = 32
MINIMUM_CANDIDATE_SCORE = 36
_WORD_OR_CJK = re.compile(r"[a-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]+")


@dataclass(frozen=True)
class RankedSemanticCandidate:
    candidate_id: str
    kind: Literal["operator", "task_method"]
    title: str
    intent_hints: tuple[str, ...]
    allowed_skill_keys: tuple[str, ...]
    available: bool
    score: int


class SemanticCandidateRanker:
    """Rank a bounded catalog with explicit hints and cheap Unicode n-grams."""

    def rank(
        self,
        contract: ScenarioContract,
        player_intent: str,
        *,
        snapshot: ScenarioSnapshot | None,
        include_task_methods: bool,
        limit: int,
    ) -> tuple[RankedSemanticCandidate, ...]:
        if limit < 1:
            raise ValueError("Semantic candidate limit must be positive")
        query = (
            ActionResolutionKernel.from_contract(contract).query_snapshot(snapshot)
            if snapshot is not None
            else None
        )
        candidates: list[RankedSemanticCandidate] = []
        unsafe_terminal_ids = unsafe_ending_operator_ids(contract)
        for operator in contract.operators:
            available = (
                operator.policy not in {"impossible", "clarification"}
                and operator.operator_id not in unsafe_terminal_ids
            )
            if query is not None:
                available = available and query.conditions_satisfied(
                    operator.preconditions
                )
            candidate_text = (
                operator.operator_id,
                operator.title,
                *operator.intent_hints,
                *(choice.skill_key for choice in operator.skill_choices),
            )
            score = self._score(player_intent, candidate_text)
            score += self._explicit_hint_coverage_bonus(
                player_intent, tuple(operator.intent_hints)
            )
            candidates.append(
                RankedSemanticCandidate(
                    candidate_id=operator.operator_id,
                    kind="operator",
                    title=operator.title,
                    intent_hints=tuple(operator.intent_hints),
                    allowed_skill_keys=tuple(
                        choice.skill_key for choice in operator.skill_choices
                    ),
                    available=available,
                    score=score,
                )
            )
        if include_task_methods:
            for method in contract.task_methods:
                available = query is None or query.conditions_satisfied(
                    method.preconditions
                )
                candidates.append(
                    RankedSemanticCandidate(
                        candidate_id=method.method_id,
                        kind="task_method",
                        title=method.title,
                        intent_hints=tuple(method.intent_hints),
                        allowed_skill_keys=(),
                        available=available,
                        score=(
                            self._score(
                                player_intent,
                                (
                                    method.method_id,
                                    method.task_key,
                                    method.title,
                                    *method.intent_hints,
                                ),
                            )
                            + self._explicit_hint_coverage_bonus(
                                player_intent, tuple(method.intent_hints)
                            )
                        ),
                    )
                )
        ordered = sorted(
            candidates,
            key=lambda item: (
                -item.score,
                not item.available,
                item.kind,
                item.candidate_id,
            ),
        )
        return tuple(ordered[:limit])

    @classmethod
    def _score(cls, intent: str, candidate_texts: tuple[str, ...]) -> int:
        normalized_intent = cls._normalize(intent)
        intent_features = cls._features(normalized_intent)
        score = 0
        seen_texts: set[str] = set()
        for raw_text in candidate_texts:
            text = cls._normalize(raw_text)
            if not text or text in seen_texts:
                continue
            seen_texts.add(text)
            if len(text) >= 2 and text in normalized_intent:
                score += 200 + min(len(text), 80)
            elif len(normalized_intent) >= 2 and normalized_intent in text:
                score += 120 + min(len(normalized_intent), 80)
            for feature in intent_features.intersection(cls._features(text)):
                score += cls._feature_weight(feature)
        return score

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(
            _WORD_OR_CJK.findall(unicodedata.normalize("NFKC", text).casefold())
        )

    @classmethod
    def _features(cls, normalized: str) -> set[str]:
        features: set[str] = set()
        for token in _WORD_OR_CJK.findall(normalized):
            if token.isascii():
                features.add(token)
                continue
            if len(token) == 1:
                features.add(token)
                continue
            features.update(token[index : index + 2] for index in range(len(token) - 1))
            if len(token) >= 3:
                features.update(
                    token[index : index + 3] for index in range(len(token) - 2)
                )
        return features

    @staticmethod
    def _feature_weight(feature: str) -> int:
        if feature.isascii():
            # Whole Latin tokens are much less collision-prone than isolated CJK
            # n-grams (for example, a full token versus a common two-character pair).
            return 32 if len(feature) >= 4 else min(max(len(feature), 2), 12)
        return 6 if len(feature) >= 3 else 3

    @classmethod
    def _explicit_hint_coverage_bonus(
        cls,
        intent: str,
        hints: tuple[str, ...],
    ) -> int:
        """Recover Chinese paraphrases whose verb and object changed order.

        Explicit intent hints are authored retrieval evidence, so a high
        character-set coverage is useful when pronouns or modifiers break all
        contiguous n-grams. Requiring four distinct CJK characters and 80%
        coverage keeps common short nouns from becoming generic interceptors.
        """

        intent_chars = {
            char for char in cls._normalize(intent) if "\u3400" <= char <= "\u9fff"
        }
        best = 0
        for hint in hints:
            hint_chars = {
                char for char in cls._normalize(hint) if "\u3400" <= char <= "\u9fff"
            }
            if len(hint_chars) < 4:
                continue
            coverage = len(intent_chars.intersection(hint_chars)) / len(hint_chars)
            if coverage >= 0.8:
                best = max(best, round(120 * coverage))
        return best


__all__ = [
    "LARGE_CANDIDATE_LIMIT",
    "MINIMUM_CANDIDATE_SCORE",
    "SMALL_CANDIDATE_LIMIT",
    "RankedSemanticCandidate",
    "SemanticCandidateRanker",
]
