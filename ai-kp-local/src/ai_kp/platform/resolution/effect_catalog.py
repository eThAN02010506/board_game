"""Closed ruleset-effect vocabulary available to executable scenario contracts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_kp.platform.resolution.dice_expression import parse_dice_expression


class ScenarioEffectParameter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    key: str = Field(min_length=1, max_length=80)
    value_type: Literal["dice_expression", "integer", "boolean", "string"]
    required: bool = True
    minimum: int | None = None
    maximum: int | None = None
    outcome_pair_separator: str | None = Field(default=None, min_length=1, max_length=4)
    source_additive_term_aliases: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_bounds(self) -> ScenarioEffectParameter:
        if self.value_type != "integer" and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("Only integer effect parameters accept numeric bounds")
        if self.outcome_pair_separator is not None and self.value_type != "dice_expression":
            raise ValueError("Only dice-expression parameters accept outcome-pair notation")
        if self.source_additive_term_aliases and self.value_type != "dice_expression":
            raise ValueError("Only dice-expression parameters accept additive source aliases")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("Effect parameter minimum cannot exceed maximum")
        return self


class ScenarioEffectCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    effect_key: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    source_aliases: tuple[str, ...] = ()
    source_preceding_blockers: tuple[str, ...] = ()
    source_following_blockers: tuple[str, ...] = ()
    parameters: tuple[ScenarioEffectParameter, ...] = ()

    @model_validator(mode="after")
    def validate_parameter_keys(self) -> ScenarioEffectCatalogEntry:
        keys = [item.key for item in self.parameters]
        if len(keys) != len(set(keys)):
            raise ValueError("Scenario effect parameter keys must be unique")
        return self

    def validate_payload(self, payload: dict[str, Any]) -> tuple[str, ...]:
        specs = {item.key: item for item in self.parameters}
        errors = [f"unknown parameter: {key}" for key in sorted(payload.keys() - specs.keys())]
        for key, spec in specs.items():
            if key not in payload:
                if spec.required:
                    errors.append(f"missing parameter: {key}")
                continue
            value = payload[key]
            if spec.value_type == "dice_expression":
                if not isinstance(value, str):
                    errors.append(f"{key} must be a bounded dice expression")
                else:
                    values = (
                        value.split(spec.outcome_pair_separator)
                        if spec.outcome_pair_separator is not None
                        else [value]
                    )
                    try:
                        if len(values) > 2:
                            raise ValueError("too many outcome values")
                        for candidate in values:
                            parse_dice_expression(candidate)
                    except ValueError as exc:
                        if "too many" in str(exc) or "safety limits" in str(exc):
                            errors.append(f"{key} exceeds dice safety limits")
                        else:
                            errors.append(f"{key} must be a bounded dice expression")
            elif spec.value_type == "integer":
                if not isinstance(value, int) or isinstance(value, bool):
                    errors.append(f"{key} must be an integer")
                elif spec.minimum is not None and value < spec.minimum:
                    errors.append(f"{key} is below {spec.minimum}")
                elif spec.maximum is not None and value > spec.maximum:
                    errors.append(f"{key} exceeds {spec.maximum}")
            elif spec.value_type == "boolean" and not isinstance(value, bool):
                errors.append(f"{key} must be a boolean")
            elif spec.value_type == "string" and (
                not isinstance(value, str) or not value.strip()
            ):
                errors.append(f"{key} must be a non-empty string")
        return tuple(errors)


class ScenarioSourceEffect(BaseModel):
    """A closed effect payload extracted deterministically from source text."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    effect_key: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any]
    outcome: Literal["always", "success", "failure", "pushed_failure"] = "always"


_SOURCE_EXPRESSION = r"(?:[1-9]\d*)?[dD][1-9]\d*(?:\s*[+-]\s*(?:(?:[1-9]\d*)?[dD][1-9]\d*|\d+))*|\d+"
_SOURCE_DICE_VALUE = re.compile(
    rf"(?<![0-9a-z])(?P<first>{_SOURCE_EXPRESSION})"
    rf"(?:\s*/\s*(?P<second>{_SOURCE_EXPRESSION}))?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _SourceDiceCandidate:
    start: int
    end: int
    first: str
    second: str | None = None


def _source_dice_candidates(
    text: str, additive_aliases: tuple[str, ...]
) -> tuple[_SourceDiceCandidate, ...]:
    candidates = [
        _SourceDiceCandidate(
            match.start(), match.end(), match.group("first"), match.group("second")
        )
        for match in _SOURCE_DICE_VALUE.finditer(text)
    ]
    atom = r"(?:[1-9]\d*)?[dD][1-9]\d*"
    for alias in additive_aliases:
        pattern = re.compile(
            rf"(?P<base>{atom})\s*\+\s*{re.escape(alias)}\s*"
            rf"(?:[（(]\s*(?P<wrapped>{atom})\s*[）)]|(?P<bare>{atom}))",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            additive = match.group("wrapped") or match.group("bare")
            candidates.append(
                _SourceDiceCandidate(
                    match.start(), match.end(), f"{match.group('base')}+{additive}"
                )
            )
    return tuple(candidates)

_PUSHED_FAILURE_MARKER = re.compile(
    r"(?:推动(?:检定)?失败|孤注一掷失败|\bpushed\s+fail(?:ure|ed)?\b)",
    re.IGNORECASE,
)
_FAILURE_MARKER = re.compile(r"(?:普通失败|失败|\b(?:on\s+)?fail(?:ure|ed)?\b)", re.IGNORECASE)
_SUCCESS_MARKER = re.compile(r"(?:困难成功|成功|\b(?:on\s+)?success(?:ful(?:ly)?)?\b)", re.IGNORECASE)


def _source_effect_outcome(
    text: str, start: int, end: int
) -> Literal["always", "success", "failure", "pushed_failure"]:
    boundaries = "。！？；;\n"
    clause_start = max((text.rfind(mark, 0, start) for mark in boundaries), default=-1) + 1
    following = [position for mark in boundaries if (position := text.find(mark, end)) >= 0]
    clause_end = min(following) if following else len(text)
    clause = text[clause_start:clause_end]
    if _PUSHED_FAILURE_MARKER.search(clause):
        return "pushed_failure"
    if _FAILURE_MARKER.search(clause):
        return "failure"
    if _SUCCESS_MARKER.search(clause):
        return "success"
    return "always"


class ScenarioEffectCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    ruleset_id: str = Field(min_length=1, max_length=120)
    ruleset_aliases: tuple[str, ...] = ()
    entries: tuple[ScenarioEffectCatalogEntry, ...] = ()

    @model_validator(mode="after")
    def validate_effect_keys(self) -> ScenarioEffectCatalog:
        keys = [item.effect_key for item in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("Scenario effect keys must be unique")
        if len(self.ruleset_aliases) != len(set(self.ruleset_aliases)):
            raise ValueError("Scenario effect ruleset aliases must be unique")
        return self

    def supports(self, ruleset_id: str) -> bool:
        return ruleset_id == self.ruleset_id or ruleset_id in self.ruleset_aliases

    def validate_effect(self, effect_key: str, payload: dict[str, Any]) -> tuple[str, ...]:
        entry = next((item for item in self.entries if item.effect_key == effect_key), None)
        if entry is None:
            return (f"unknown ruleset effect: {effect_key}",)
        return entry.validate_payload(payload)

    def split_outcome_payload(
        self,
        effect_key: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Expand a ruleset-declared source shorthand into success/failure payloads."""

        entry = next((item for item in self.entries if item.effect_key == effect_key), None)
        if entry is None:
            return None
        pair_parameters = tuple(
            item for item in entry.parameters if item.outcome_pair_separator is not None
        )
        if len(pair_parameters) != 1:
            return None
        parameter = pair_parameters[0]
        value = payload.get(parameter.key)
        separator = parameter.outcome_pair_separator
        if not isinstance(value, str) or separator is None or value.count(separator) != 1:
            return None
        success_value, failure_value = (item.strip() for item in value.split(separator))
        if not success_value or not failure_value:
            return None
        success_payload = {**payload, parameter.key: success_value}
        failure_payload = {**payload, parameter.key: failure_value}
        if entry.validate_payload(success_payload) or entry.validate_payload(failure_payload):
            return None
        return success_payload, failure_payload

    def extract_source_effects(self, text: str) -> tuple[ScenarioSourceEffect, ...]:
        """Extract plugin-declared dice payloads adjacent to source effect aliases.

        Scenario prose commonly places the value on either side of the effect
        name (for example, ``damage 1d6`` and ``1d6 damage``).  The catalog owns
        the vocabulary, while this parser only selects the nearest bounded dice
        expression in a small window; it does not infer an effect from prose.
        """

        normalized_text = unicodedata.normalize("NFKC", text)
        effects: list[ScenarioSourceEffect] = []
        seen: set[tuple[str, str]] = set()
        for entry in self.entries:
            dice_parameters = tuple(
                item for item in entry.parameters if item.value_type == "dice_expression"
            )
            if len(dice_parameters) != 1 or not entry.source_aliases:
                continue
            parameter = dice_parameters[0]
            for alias in entry.source_aliases:
                normalized_alias = unicodedata.normalize("NFKC", alias)
                for alias_match in re.finditer(
                    re.escape(normalized_alias), normalized_text, re.IGNORECASE
                ):
                    if any(
                        len(additive_alias) > len(normalized_alias)
                        and normalized_text[
                            alias_match.start() : alias_match.start() + len(additive_alias)
                        ].casefold()
                        == unicodedata.normalize("NFKC", additive_alias).casefold()
                        for additive_alias in parameter.source_additive_term_aliases
                    ):
                        # A base effect alias may be a lexical prefix of a
                        # catalog-declared additive term ("damage" in
                        # "damage bonus"). The term is not itself an effect.
                        continue
                    preceding = normalized_text[
                        max(0, alias_match.start() - 16) : alias_match.start()
                    ].casefold()
                    following = normalized_text[
                        alias_match.end() : alias_match.end() + 16
                    ].casefold()
                    if any(
                        unicodedata.normalize("NFKC", blocker).casefold() in preceding
                        for blocker in entry.source_preceding_blockers
                    ) or any(
                        unicodedata.normalize("NFKC", blocker).casefold() in following
                        for blocker in entry.source_following_blockers
                    ):
                        continue
                    window_start = max(0, alias_match.start() - 48)
                    window_end = min(len(normalized_text), alias_match.end() + 48)
                    nearby = normalized_text[window_start:window_end]
                    alias_start = alias_match.start() - window_start
                    alias_end = alias_match.end() - window_start
                    candidates = _source_dice_candidates(
                        nearby, parameter.source_additive_term_aliases
                    )
                    if not candidates:
                        continue
                    value_match = min(
                        candidates,
                        key=lambda match: (
                            alias_start - match.end
                            if match.end <= alias_start
                            else match.start - alias_end
                            if match.start >= alias_end
                            else 0,
                            # Prefer the conventional alias-then-value form if
                            # equally close, while still accepting Chinese prose
                            # that places the dice expression first.
                            0 if match.start >= alias_end else 1,
                            -(match.end - match.start),
                        ),
                    )
                    clause_start = max(
                        normalized_text.rfind(mark, 0, window_start + value_match.start)
                        for mark in "。！？；;\n"
                    ) + 1
                    clause_end_candidates = [
                        position
                        for mark in "。！？；;\n"
                        if (
                            position := normalized_text.find(
                                mark, window_start + value_match.end
                            )
                        )
                        >= 0
                    ]
                    clause_end = (
                        min(clause_end_candidates)
                        if clause_end_candidates
                        else len(normalized_text)
                    )
                    clause = normalized_text[clause_start:clause_end]
                    if re.search(
                        r"(?:例如|示例|举例|比如|譬如|\be\.?g\.?\b|\bfor example\b)",
                        clause,
                        re.IGNORECASE,
                    ):
                        continue
                    first = parse_dice_expression(value_match.first).normalized
                    second = value_match.second
                    value = (
                        f"{first}{parameter.outcome_pair_separator}"
                        f"{parse_dice_expression(second).normalized}"
                        if second is not None
                        and parameter.outcome_pair_separator is not None
                        else first
                    )
                    payload = {parameter.key: value}
                    if entry.validate_payload(payload) and self.split_outcome_payload(
                        entry.effect_key, payload
                    ) is None:
                        continue
                    signature = (entry.effect_key, value)
                    if signature in seen:
                        continue
                    seen.add(signature)
                    effects.append(
                        ScenarioSourceEffect(
                            effect_key=entry.effect_key,
                            payload=payload,
                            outcome=_source_effect_outcome(
                                normalized_text,
                                min(alias_match.start(), window_start + value_match.start),
                                max(alias_match.end(), window_start + value_match.end),
                            ),
                        )
                    )
        return tuple(effects)


__all__ = [
    "ScenarioEffectCatalog",
    "ScenarioEffectCatalogEntry",
    "ScenarioEffectParameter",
    "ScenarioSourceEffect",
]
