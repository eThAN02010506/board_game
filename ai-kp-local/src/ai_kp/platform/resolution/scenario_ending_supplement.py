"""Constrained ending links over kernel outcomes and advertised state."""

from __future__ import annotations

import json
import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_kp.platform.resolution.contracts import ConditionOperator, StateCondition
from ai_kp.platform.resolution.kernel import operator_outcome_path
from ai_kp.platform.resolution.scenario_ir_models import (
    IrEnding,
    ScenarioIrBatch,
)
from ai_kp.platform.resolution.source_coverage import SourceCoverageSupplementTarget

SERVER_COVERAGE_ENDING_ASSUMPTION_PREFIX = (
    "Deterministic source ending materialized by the server: "
)

_ZH_CONDITION = re.compile(
    r"((?:如果|若|一旦|当(?!然)).{2,180}?)(?=[，,])"
)
_EN_CONDITION = re.compile(
    r"\b((?:if|when|once)\s+.{2,180}?)(?=[,;])", re.IGNORECASE
)
_NEGATED_OUTCOME = re.compile(
    r"(?:未能|没有|不能|失败|未|不|\b(?:fail(?:ed)?|not|unable)\b)",
    re.IGNORECASE,
)
_COMPACT_TITLE = re.compile(r"[^\w\u3400-\u9fff]+", re.UNICODE)
_ENDING_ALIAS = Annotated[str, Field(min_length=1, max_length=240)]


class EndingOperatorCandidate(BaseModel):
    """Server-selected operator whose outcomes may trigger an ending."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    operator_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    aliases: tuple[_ENDING_ALIAS, ...] = Field(default=(), max_length=12)
    allowed_outcomes: tuple[str, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_aliases(self) -> EndingOperatorCandidate:
        identities = [_compact_phrase(item) for item in self.aliases]
        if len(identities) != len(set(identities)):
            raise ValueError("Ending operator aliases must be unique")
        return self


class EndingOutcomeSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    operator_id: str = Field(min_length=1, max_length=160)
    outcome: str = Field(min_length=1, max_length=120)


class EndingStateProducer(EndingOutcomeSelector):
    """Executable operator outcome that reaches one advertised state value."""

    value: Any = None
    repeatable_delta: int | float | None = None

    @model_validator(mode="after")
    def validate_value(self) -> EndingStateProducer:
        if not _is_scalar(self.value):
            raise ValueError("Ending state producer value must be a finite JSON scalar")
        if self.repeatable_delta is not None and (
            not isinstance(self.repeatable_delta, (int, float))
            or isinstance(self.repeatable_delta, bool)
            or not math.isfinite(self.repeatable_delta)
            or self.repeatable_delta == 0
        ):
            raise ValueError("Ending repeatable delta must be a finite non-zero number")
        return self


class EndingStateCandidate(BaseModel):
    """Server-advertised authoritative state that may constrain an ending."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    path: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=240)
    allowed_operators: tuple[ConditionOperator, ...] = Field(min_length=1, max_length=8)
    value_kind: Literal["number", "scalar"]
    initial_value: int | float | None = None
    minimum_value: int | float | None = None
    maximum_value: int | float | None = None
    allowed_values: tuple[Any, ...] = Field(default=(), max_length=16)
    producers: tuple[EndingStateProducer, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_value_policy(self) -> EndingStateCandidate:
        comparisons = {"eq", "ne", "gt", "gte", "lt", "lte"}
        if not set(self.allowed_operators) <= comparisons:
            raise ValueError("Ending state candidates only support scalar comparisons")
        if self.value_kind == "number":
            for bound in (
                self.initial_value,
                self.minimum_value,
                self.maximum_value,
            ):
                if bound is not None and not math.isfinite(bound):
                    raise ValueError("Ending numeric bounds must be finite")
            if (
                self.minimum_value is not None
                and self.maximum_value is not None
                and self.minimum_value > self.maximum_value
            ):
                raise ValueError("Ending numeric bounds are inverted")
            if self.allowed_values:
                raise ValueError("Numeric ending candidates use bounds, not allowed_values")
        elif (
            self.initial_value is not None
            or self.minimum_value is not None
            or self.maximum_value is not None
        ):
            raise ValueError("Scalar ending candidates cannot declare numeric bounds")
        if self.value_kind == "scalar" and not self.allowed_values:
            raise ValueError("Scalar ending candidates require server-advertised values")
        for value in self.allowed_values:
            if not _is_scalar(value):
                raise ValueError("Ending state values must be finite JSON scalars")
        for producer in self.producers:
            scalar_value_allowed = self.value_kind == "scalar" and any(
                type(producer.value) is type(value) and producer.value == value
                for value in self.allowed_values
            )
            numeric_value_allowed = (
                self.value_kind == "number"
                and isinstance(producer.value, (int, float))
                and not isinstance(producer.value, bool)
                and (
                    self.minimum_value is None
                    or producer.value >= self.minimum_value
                )
                and (
                    self.maximum_value is None
                    or producer.value <= self.maximum_value
                )
            )
            if self.value_kind == "scalar" and producer.repeatable_delta is not None:
                raise ValueError("Scalar ending producers cannot be repeatable deltas")
            if not scalar_value_allowed and not numeric_value_allowed:
                raise ValueError(
                    "Ending state producer value must satisfy its advertised state policy"
                )
        producer_identities = [
            (
                item.operator_id,
                item.outcome,
                _value_identity(item.value),
                item.repeatable_delta,
            )
            for item in self.producers
        ]
        if len(producer_identities) != len(set(producer_identities)):
            raise ValueError("Ending state producers must be unique")
        return self


class EndingStateSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    path: str = Field(min_length=1, max_length=240)
    operator: ConditionOperator
    value: Any = None


class CoverageEndingProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=240)
    all_of: tuple[EndingOutcomeSelector, ...] = Field(default=(), max_length=8)
    any_of: tuple[EndingOutcomeSelector, ...] = Field(default=(), max_length=8)
    state_all_of: tuple[EndingStateSelector, ...] = Field(default=(), max_length=8)
    state_any_of: tuple[EndingStateSelector, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def require_conditions(self) -> CoverageEndingProposal:
        if not (self.all_of or self.any_of or self.state_all_of or self.state_any_of):
            raise ValueError("An ending proposal requires an authoritative condition")
        selectors = (*self.all_of, *self.any_of)
        identities = [(item.operator_id, item.outcome) for item in selectors]
        if len(identities) != len(set(identities)):
            raise ValueError("An ending proposal cannot repeat an operator outcome")
        state_selectors = (*self.state_all_of, *self.state_any_of)
        state_identities = [
            (item.path, item.operator, _value_identity(item.value))
            for item in state_selectors
        ]
        if len(state_identities) != len(set(state_identities)):
            raise ValueError("An ending proposal cannot repeat a state condition")
        return self


def _is_scalar(value: Any) -> bool:
    return (
        value is None
        or isinstance(value, (str, bool, int))
        or isinstance(value, float) and math.isfinite(value)
    )


def _value_identity(value: Any) -> str:
    if not _is_scalar(value):
        return f"invalid:{type(value).__name__}"
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class CoverageEndingEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    endings: tuple[CoverageEndingProposal, ...] = Field(min_length=1, max_length=8)


_COVERAGE_ENDING_SCHEMA_JSON = json.dumps(
    CoverageEndingEnvelope.model_json_schema(),
    ensure_ascii=False,
    separators=(",", ":"),
)


def coverage_ending_schema_json() -> str:
    return _COVERAGE_ENDING_SCHEMA_JSON


def narrow_ending_envelope_payload(payload: Any) -> Any:
    """Wrap an unambiguous single proposal without changing its semantics."""

    if not isinstance(payload, dict) or "endings" in payload:
        return payload
    proposal_fields = {
        "title",
        "all_of",
        "any_of",
        "state_all_of",
        "state_any_of",
    }
    if "title" in payload and set(payload) <= proposal_fields:
        return {"endings": [payload]}
    return payload


def supports_ending_materialization(
    targets: tuple[SourceCoverageSupplementTarget, ...],
) -> bool:
    return bool(targets) and all(
        target.requirement_key == "ending_rule"
        and target.acceptable_record_kinds == ("endings",)
        for target in targets
    )


def deterministic_ending_envelope(
    text: str,
    *,
    candidates: tuple[EndingOperatorCandidate, ...] = (),
    state_candidates: tuple[EndingStateCandidate, ...] = (),
) -> CoverageEndingEnvelope | None:
    """Parse a source-explicit conditional ending without model-owned state."""

    match = _ZH_CONDITION.search(text) or _EN_CONDITION.search(text)
    if match is None:
        return None
    clause = re.sub(r"[*_`#]+", "", match.group(1)).strip()[:240]
    candidate_matches: list[
        tuple[int, EndingOperatorCandidate, tuple[int, int, int]]
    ] = []
    for candidate_index, candidate in enumerate(candidates):
        phrases = tuple(dict.fromkeys((candidate.title, *candidate.aliases)))
        matches = [
            matched
            for phrase in phrases
            if (matched := _match_phrase(clause, phrase)) is not None
        ]
        if not matches:
            continue
        # Prefer the most specific server-owned expression.  Multiple aliases for
        # one operator still produce exactly one authoritative selector.
        candidate_matches.append(
            (candidate_index, candidate, max(matches, key=lambda item: item[0]))
        )

    selectors: list[EndingOutcomeSelector] = []
    occupied_spans: list[tuple[int, int]] = []
    selected_operator_ids: set[str] = set()
    for _, candidate, (_, start, end) in sorted(
        candidate_matches,
        key=lambda item: (-item[2][0], item[0]),
    ):
        if candidate.operator_id in selected_operator_ids or any(
            start < occupied_end and occupied_start < end
            for occupied_start, occupied_end in occupied_spans
        ):
            continue
        # Claim the most specific source phrase even when its requested outcome
        # is unavailable. Falling through to a shorter overlapping operator would
        # silently change which causal action the source names.
        occupied_spans.append((start, end))
        prefix = clause[max(0, start - 24) : start]
        requested = "failure" if _NEGATED_OUTCOME.search(prefix) else "success"
        if requested in candidate.allowed_outcomes:
            selectors.append(
                EndingOutcomeSelector(
                    operator_id=candidate.operator_id,
                    outcome=requested,
                )
            )
            selected_operator_ids.add(candidate.operator_id)
        if len(selectors) >= 4:
            break
    state_selectors: list[EndingStateSelector] = []
    for candidate in state_candidates:
        if not candidate.producers:
            continue
        phrases = tuple(dict.fromkeys((candidate.title, candidate.path)))
        matches = [
            matched
            for phrase in phrases
            if (matched := _match_phrase(clause, phrase)) is not None
        ]
        if not matches:
            continue
        _, start, end = max(matches, key=lambda item: item[0])
        context = clause[max(0, start - 16) : min(len(clause), end + 24)]
        selected_value: Any
        context_start = max(0, start - 16)
        prefix = context[: start - context_start]
        if re.search(r"(?:为|是)\s*(?:假|false\b)", context, re.IGNORECASE):
            selected_value = False
        elif re.search(r"(?:为|是)\s*(?:真|true\b)", context, re.IGNORECASE):
            selected_value = True
        elif _NEGATED_OUTCOME.search(prefix):
            selected_value = False
        else:
            # A bare boolean fact in an if/when clause conventionally asserts it.
            # Other scalar and numeric comparisons stay model-owned because the
            # server cannot recover their operator/value without guessing.
            selected_value = True
        if not any(
            type(selected_value) is type(item) and selected_value == item
            for item in candidate.allowed_values
        ):
            continue
        state_selectors.append(EndingStateSelector(
            path=candidate.path,
            operator="eq",
            value=selected_value,
        ))
        occupied_spans.append((start, end))
        for producer in candidate.producers:
            if not (
                type(producer.value) is type(selected_value)
                and producer.value == selected_value
            ):
                continue
            selector = EndingOutcomeSelector(
                operator_id=producer.operator_id,
                outcome=producer.outcome,
            )
            if selector not in selectors:
                selectors.append(selector)
        if not selectors:
            state_selectors.clear()
        break
    # A source sentence is evidence for an ending, not evidence that the ending
    # has happened.  If no already executable operator proves the condition, the
    # authoring job must remain incomplete and request a real causal operator.
    if not selectors and not state_selectors:
        return None
    if not _condition_clause_fully_bound(clause, occupied_spans):
        return None
    proposal = CoverageEndingProposal(
        title=clause,
        all_of=tuple(selectors),
        state_all_of=tuple(state_selectors),
    )
    return CoverageEndingEnvelope(endings=(proposal,))


def _compact_phrase(value: str) -> str:
    return _COMPACT_TITLE.sub("", value).casefold()


def _condition_clause_fully_bound(
    clause: str,
    occupied_spans: list[tuple[int, int]],
) -> bool:
    """Reject deterministic endings that only bind part of a source condition."""

    uncovered = list(clause)
    for start, end in occupied_spans:
        uncovered[start:end] = " " * (end - start)
    remainder = "".join(uncovered)
    remainder = re.sub(
        r"(?:如果|若|一旦|当|则|就|且|并且|以及|与|和|或|既|也|"
        r"未能|没有|不能|失败|未|不|为|是|真|假|"
        r"\b(?:if|when|once|then|and|or|not|true|false|failed?|unable|is)\b)",
        " ",
        remainder,
        flags=re.IGNORECASE,
    )
    return not _compact_phrase(remainder)


def _match_phrase(clause: str, phrase: str) -> tuple[int, int, int] | None:
    """Return compact specificity and source position for an exact phrase match."""

    compact = _compact_phrase(phrase)
    if len(compact) < 4:
        return None
    # Underscores are formatting separators in source prose and identifiers,
    # not semantic characters. This mirrors ``_compact_phrase`` and lets an
    # authoritative path match escaped Markdown such as ``case\_resolved``.
    characters = re.findall(r"[^\W_]", phrase.casefold(), re.UNICODE)
    if not characters:
        return None
    pattern = r"[\W_]*".join(re.escape(character) for character in characters)
    # Latin aliases must match whole words; compact substring matching would make
    # `restore light` incorrectly match `restore lighthouse`.
    if compact.isascii():
        pattern = rf"(?<!\w){pattern}(?!\w)"
    matched = re.search(pattern, clause, re.IGNORECASE | re.UNICODE)
    if matched is None:
        return None
    return len(compact), matched.start(), matched.end()


def materialize_ending_envelope(
    envelope: CoverageEndingEnvelope,
    *,
    candidates: tuple[EndingOperatorCandidate, ...],
    state_candidates: tuple[EndingStateCandidate, ...] = (),
    source_block_id: str,
    record_id_prefix: str,
) -> ScenarioIrBatch:
    """Build endings only from server-advertised outcomes and state."""

    allowed = {
        candidate.operator_id: set(candidate.allowed_outcomes)
        for candidate in candidates
    }
    allowed_states: dict[str, EndingStateCandidate] = {}
    for candidate in state_candidates:
        if candidate.path in allowed_states:
            raise ValueError(f"Duplicate ending state candidate: {candidate.path}")
        allowed_states[candidate.path] = candidate
        for producer in candidate.producers:
            allowed.setdefault(producer.operator_id, set()).add(producer.outcome)

    def condition(selector: EndingOutcomeSelector) -> StateCondition:
        outcomes = allowed.get(selector.operator_id)
        if outcomes is None or selector.outcome not in outcomes:
            raise ValueError(
                "Ending selector is outside the server operator outcome catalog: "
                f"{selector.operator_id}/{selector.outcome}"
            )
        return StateCondition(
            path=operator_outcome_path(selector.operator_id),
            operator="eq",
            value=selector.outcome,
        )

    def state_condition(selector: EndingStateSelector) -> StateCondition:
        candidate = allowed_states.get(selector.path)
        if candidate is None or selector.operator not in candidate.allowed_operators:
            raise ValueError(
                "Ending state selector is outside the server condition catalog: "
                f"{selector.path}/{selector.operator}"
            )
        operator = selector.operator
        value = selector.value
        if not _is_scalar(value):
            raise ValueError("Ending state selector value must be a finite JSON scalar")
        if candidate.value_kind == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError("Ending numeric state selector requires a number")
            if candidate.minimum_value is not None and value < candidate.minimum_value:
                raise ValueError("Ending state selector is below the server minimum")
            if candidate.maximum_value is not None and value > candidate.maximum_value:
                raise ValueError("Ending state selector exceeds the server maximum")
        else:
            allowed = candidate.allowed_values
            if (
                len(allowed) == 1
                and isinstance(allowed[0], bool)
                and isinstance(value, bool)
                and value is not allowed[0]
                and operator in {"eq", "ne"}
            ):
                # Facts often represent a discovered flag by absence until the
                # sole advertised boolean is written. Convert a weak model's
                # ``eq false`` into the authoritative open-world form ``ne true``
                # (and vice versa) without changing the requested truth condition.
                operator = "ne" if operator == "eq" else "eq"
                value = allowed[0]
            if not any(
                type(value) is type(item) and value == item for item in allowed
            ):
                raise ValueError("Ending state selector value is outside the server catalog")
        return StateCondition(
            path=selector.path,
            operator=operator,
            value=value,
        )

    def selector_matches_value(selector: EndingStateSelector, value: Any) -> bool:
        if selector.operator == "eq":
            return value == selector.value
        if selector.operator == "ne":
            return value != selector.value
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        if selector.operator == "gt":
            return value > selector.value
        if selector.operator == "gte":
            return value >= selector.value
        if selector.operator == "lt":
            return value < selector.value
        if selector.operator == "lte":
            return value <= selector.value
        return False

    def validate_numeric_compatibility(proposal: CoverageEndingProposal) -> None:
        selected_outcomes = {
            (item.operator_id, item.outcome)
            for item in (*proposal.all_of, *proposal.any_of)
        }
        for selector in (*proposal.state_all_of, *proposal.state_any_of):
            candidate = allowed_states.get(selector.path)
            if (
                candidate is None
                or candidate.value_kind != "number"
                or candidate.initial_value is None
            ):
                continue
            reachable_values = [candidate.initial_value]
            reachable_values.extend(
                producer.value
                for producer in candidate.producers
                if (producer.operator_id, producer.outcome) in selected_outcomes
            )
            if not any(
                selector_matches_value(selector, value)
                for value in reachable_values
            ) and not any(
                repeatable_selector_reachable(selector, candidate, producer)
                for producer in candidate.producers
                if (producer.operator_id, producer.outcome) in selected_outcomes
                and producer.repeatable_delta is not None
            ):
                raise ValueError(
                    "Ending numeric state selector is incompatible with its "
                    f"selected operator outcomes: {selector.path}"
                )

    def repeatable_selector_reachable(
        selector: EndingStateSelector,
        candidate: EndingStateCandidate,
        producer: EndingStateProducer,
    ) -> bool:
        start = candidate.initial_value
        delta = producer.repeatable_delta
        if start is None or delta is None:
            return False
        bound = (
            candidate.maximum_value
            if delta > 0
            else candidate.minimum_value
        )
        if bound is None:
            return False
        if selector.operator != "eq":
            return selector_matches_value(selector, bound)
        target = selector.value
        if not isinstance(target, (int, float)) or isinstance(target, bool):
            return False
        if target == bound:
            return True
        steps = (target - start) / delta
        return steps >= 1 and math.isclose(steps, round(steps))

    endings: list[IrEnding] = []
    for index, proposal in enumerate(envelope.endings, start=1):
        validate_numeric_compatibility(proposal)
        endings.append(
            IrEnding(
                id=f"{record_id_prefix}ending_{index:02d}",
                title=proposal.title,
                all_conditions=(
                    *(condition(item) for item in proposal.all_of),
                    *(state_condition(item) for item in proposal.state_all_of),
                ),
                any_conditions=(
                    *(condition(item) for item in proposal.any_of),
                    *(state_condition(item) for item in proposal.state_any_of),
                ),
                source_block_ids=(source_block_id,),
            )
        )
    return ScenarioIrBatch(
        confidence="high",
        endings=tuple(endings),
    )


__all__ = [
    "SERVER_COVERAGE_ENDING_ASSUMPTION_PREFIX",
    "CoverageEndingEnvelope",
    "EndingOperatorCandidate",
    "EndingStateCandidate",
    "EndingStateProducer",
    "coverage_ending_schema_json",
    "deterministic_ending_envelope",
    "materialize_ending_envelope",
    "narrow_ending_envelope_payload",
    "supports_ending_materialization",
]
