"""Ruleset-provided, deterministic mapping from source check terms to target keys."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def normalize_check_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", normalized)


def _check_term_variants(value: str) -> tuple[str, ...]:
    normalized = normalize_check_term(value)
    variants = [normalized]
    without_suffix = re.sub(r"(?:检定|判定|鉴定|check|test)$", "", normalized)
    variants.append(without_suffix)
    variants.append(re.sub(r"^(?:普通|困难|极难)", "", without_suffix))
    return tuple(dict.fromkeys(item for item in variants if item))


def check_term_occurs_in_text(term: str, text: str) -> bool:
    """Require a model-extracted check term to be present in its cited source text."""

    normalized_text = normalize_check_term(text)
    casefolded_text = unicodedata.normalize("NFKC", text).casefold()
    for variant in _check_term_variants(term):
        if variant.isascii() and variant.isalnum():
            if re.search(rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z])", casefolded_text):
                return True
        elif variant in normalized_text:
            return True
    return False


def _term_has_check_context(
    term: str,
    text: str,
    resolved_keys: set[str],
) -> bool:
    normalized_term = unicodedata.normalize("NFKC", term).casefold()
    normalized_text = unicodedata.normalize("NFKC", text).casefold()
    marker = r"(?:检定|判定|鉴定|check|test|成功|失败)"
    for match in re.finditer(re.escape(normalized_term), normalized_text):
        # Imported documents often retain a long introductory clause or insert
        # Markdown around the printed skill name. Check a bounded local window
        # rather than rejecting the entire clause because of its total length.
        # The bound still prevents a distant "success" elsewhere in a paragraph
        # from granting check authority to an unrelated term.
        window_start = max(0, match.start() - 24)
        window_end = min(len(normalized_text), match.end() + 24)
        left = normalized_text[window_start : match.start()]
        right = normalized_text[match.end() : window_end]
        separators = "，。；;。\n"
        left_boundary = max((left.rfind(char) for char in separators), default=-1)
        right_offsets = tuple(
            offset for char in separators if (offset := right.find(char)) >= 0
        )
        right_boundary = min(right_offsets, default=len(right))
        clause = left[left_boundary + 1 :] + normalized_term + right[:right_boundary]
        escaped = re.escape(normalized_term)
        if re.search(
            rf"(?:{escaped}.{{0,20}}{marker}|{marker}.{{0,20}}{escaped})",
            clause,
            re.IGNORECASE,
        ):
            return True
    return "san" in resolved_keys and bool(
        re.search(
            rf"{re.escape(normalized_term)}.{{0,12}}"
            r"(?:损失|减少|降低|扣除|\d+\s*/\s*(?:\d+|\d*d\d+))",
            normalized_text,
            re.IGNORECASE,
        )
    )


class ScenarioCheckCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    check_key: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=120)
    direct_aliases: tuple[str, ...] = ()
    concept_aliases: tuple[str, ...] = ()


class ScenarioSourceCheck(BaseModel):
    """A check candidate whose authority comes from the cited source text."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    term: str = Field(min_length=1, max_length=120)
    difficulty: Literal["regular", "hard", "extreme"] = "regular"


def _source_difficulty(term: str, text: str) -> Literal["regular", "hard", "extreme"]:
    normalized_term = unicodedata.normalize("NFKC", term).casefold()
    normalized_text = unicodedata.normalize("NFKC", text).casefold()
    positions = tuple(
        match.start() for match in re.finditer(re.escape(normalized_term), normalized_text)
    )
    for position in positions:
        left = normalized_text[:position]
        right = normalized_text[position + len(normalized_term) :]
        left_boundary = max((left.rfind(marker) for marker in "，。；;、或"), default=-1)
        right_offsets = tuple(
            offset for marker in "，。；;、或" if (offset := right.find(marker)) >= 0
        )
        right_boundary = min(right_offsets, default=len(right))
        context = (
            left[max(left_boundary + 1, len(left) - 12) :]
            + normalized_term
            + right[: min(right_boundary, 8)]
        )
        if "极难" in context or "extreme" in context:
            return "extreme"
        if "困难" in context or "hard" in context:
            return "hard"
    return "regular"


class ScenarioCheckCatalog(BaseModel):
    """Closed ruleset vocabulary; broad concepts may deliberately yield choices."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ruleset_id: str = Field(min_length=1, max_length=120)
    entries: tuple[ScenarioCheckCatalogEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_keys(self) -> ScenarioCheckCatalog:
        keys = [entry.check_key for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("Scenario check catalog keys must be unique")
        return self

    def resolve(self, term: str) -> tuple[str, ...]:
        """Prefer an exact key/name/alias; otherwise return all concept matches."""

        direct = self.resolve_direct(term)
        if direct:
            return direct
        wanted = set(_check_term_variants(term))
        if not wanted:
            return ()

        concepts = [
            entry.check_key
            for entry in self.entries
            if wanted
            & {normalize_check_term(alias) for alias in entry.concept_aliases}
        ]
        return tuple(dict.fromkeys(concepts))

    def resolve_direct(self, term: str) -> tuple[str, ...]:
        """Resolve only an explicit key, display name, or direct alias."""

        wanted = set(_check_term_variants(term))
        if not wanted:
            return ()
        direct = [
            entry.check_key
            for entry in self.entries
            if wanted
            & {
                normalize_check_term(entry.check_key),
                normalize_check_term(entry.display_name),
                *(normalize_check_term(alias) for alias in entry.direct_aliases),
            }
        ]
        return tuple(dict.fromkeys(direct))

    def contains(self, check_key: str) -> bool:
        return any(entry.check_key == check_key for entry in self.entries)

    def source_present_keys(self, text: str) -> tuple[str, ...]:
        """Return keys whose direct ruleset names occur in this exact source."""

        return tuple(
            entry.check_key
            for entry in self.entries
            if any(
                check_term_occurs_in_text(candidate, text)
                for candidate in (
                    entry.display_name,
                    *entry.direct_aliases,
                    entry.check_key,
                )
            )
        )

    def extract_source_checks(self, text: str) -> tuple[ScenarioSourceCheck, ...]:
        """Find source-present checks and source-owned difficulty settings."""

        matched_checks: list[ScenarioSourceCheck] = []
        seen_terms: set[str] = set()
        covered_keys: set[str] = set()
        for entry in self.entries:
            candidates = (
                entry.display_name,
                *entry.direct_aliases,
                *entry.concept_aliases,
                entry.check_key,
            )
            for candidate in candidates:
                normalized = normalize_check_term(candidate)
                if normalized in seen_terms or not check_term_occurs_in_text(
                    candidate, text
                ):
                    continue
                seen_terms.add(normalized)
                resolved = set(self.resolve(candidate))
                if (
                    not resolved
                    or resolved <= covered_keys
                    or not _term_has_check_context(candidate, text, resolved)
                ):
                    continue
                matched_checks.append(
                    ScenarioSourceCheck(
                        term=candidate,
                        difficulty=_source_difficulty(candidate, text),
                    )
                )
                covered_keys.update(resolved)
        return tuple(matched_checks)

    def extract_source_terms(self, text: str) -> tuple[str, ...]:
        """Compatibility view for callers that only need source terms."""

        return tuple(item.term for item in self.extract_source_checks(text))


__all__ = [
    "ScenarioCheckCatalog",
    "ScenarioCheckCatalogEntry",
    "ScenarioSourceCheck",
    "check_term_occurs_in_text",
    "normalize_check_term",
]
