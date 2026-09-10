"""Deterministic, source-local outcome prose extraction for checked actions."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.platform.resolution.contracts import WorldCommand

OutcomeKey = Literal["success", "failure", "pushed_failure"]

_OUTCOME_MARKER = re.compile(
    r"(?P<pushed>(?:推动(?:检定)?|孤注一掷)失败|pushed\s+fail(?:ure|ed)?)"
    r"|(?P<failure>(?:普通)?失败|(?:on\s+)?fail(?:ure|ed)?)"
    r"|(?P<success>(?:困难|极难|普通)?成功|(?:on\s+)?success(?:ful(?:ly)?)?)",
    re.IGNORECASE,
)
_PUSH_AUTHORIZATION = re.compile(
    r"(?:可(?:以)?推动|允许推动|可(?:以)?孤注一掷|"
    r"推动(?:检定)?失败|孤注一掷失败|may\s+(?:be\s+)?push(?:ed)?|"
    r"can\s+(?:be\s+)?push(?:ed)?|pushed\s+fail(?:ure|ed)?)",
    re.IGNORECASE,
)
_TERMINATOR = re.compile(r"[\n。！？;；]")
_EXPLICIT_FACT_ASSIGNMENT = re.compile(
    r"(?:"
    r"设置(?:事实|状态)\s*[:：]?\s*"
    r"|set(?:s|ting)?\s+(?:the\s+)?(?:fact|state)\s+"
    r")"
    r"(?P<path>[A-Za-z][A-Za-z0-9_.-]{0,119})"
    r"(?:\s*(?:为|=|to)\s*(?P<value>true|false|真|假))?",
    re.IGNORECASE,
)


class SourceOutcomeClause(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    outcome_key: OutcomeKey
    public_summary: str = Field(min_length=1, max_length=1200)


class SourceFactAssignment(BaseModel):
    """A source-authored boolean state mutation, not a model interpretation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    path: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,119}$")
    value: bool


def unspecified_outcome_summary(action_title: str, outcome: OutcomeKey) -> str:
    """Return fixed public wording that explicitly declines to invent effects."""

    if outcome == "success":
        return (
            f"本次尝试已达成“{action_title}”所述目标；"
            "来源未规定更具体的奖励、线索或数值结果。"
        )
    if outcome == "failure":
        return (
            f"本次尝试未达成“{action_title}”所述目标；"
            "来源保证的自动信息仍保留，且未规定其他普通失败后果。"
        )
    return (
        f"推动检定未达成“{action_title}”所述目标；"
        "来源未规定更具体的推动失败后果。"
    )


def action_goal_boundary_command(
    action_id: str,
    outcome: OutcomeKey,
) -> WorldCommand:
    """Record only whether the source-named action goal was achieved."""

    return WorldCommand(
        kind="set_fact",
        path=f"action_goals.{action_id}.achieved",
        value=outcome == "success",
    )


def is_action_goal_boundary_command(
    command: WorldCommand,
    *,
    action_id: str,
    outcome: OutcomeKey,
) -> bool:
    expected = action_goal_boundary_command(action_id, outcome)
    return command == expected


def normalized_source_contains(source: str, excerpt: str) -> bool:
    """Compare source prose after only lossless formatting normalization."""

    def normalize(value: str) -> str:
        return re.sub(
            r"\s+", " ", unicodedata.normalize("NFKC", value)
        ).strip().casefold()

    wanted = normalize(excerpt)
    return bool(wanted) and wanted in normalize(source)


def extract_source_outcome_clauses(text: str) -> tuple[SourceOutcomeClause, ...]:
    """Return explicitly labelled outcome clauses without interpreting their prose.

    Each excerpt starts at its printed outcome marker and ends at the next marker or
    sentence boundary.  Unlabelled implications deliberately produce no result.
    """

    matches = tuple(_OUTCOME_MARKER.finditer(text))
    clauses: list[SourceOutcomeClause] = []
    seen: set[OutcomeKey] = set()
    for index, match in enumerate(matches):
        outcome: OutcomeKey = (
            "pushed_failure"
            if match.lastgroup == "pushed"
            else "failure"
            if match.lastgroup == "failure"
            else "success"
        )
        if outcome in seen:
            continue
        next_marker = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        terminator = _TERMINATOR.search(text, match.end(), next_marker)
        end = terminator.start() if terminator is not None else next_marker
        excerpt = text[match.start() : end].strip(" \t\r\n，,:：")[:1200]
        # A bare label is not an observable result.
        remainder = text[match.end() : end].strip(" \t\r\n，,:：")
        if not excerpt or not remainder:
            continue
        clauses.append(
            SourceOutcomeClause(outcome_key=outcome, public_summary=excerpt)
        )
        seen.add(outcome)
    return tuple(clauses)


def source_authorizes_push(text: str) -> bool:
    return bool(_PUSH_AUTHORIZATION.search(text))


def extract_explicit_fact_assignments(text: str) -> tuple[SourceFactAssignment, ...]:
    """Extract only explicit boolean assignments printed by the module source.

    A bare ``set fact X`` is the conventional positive assignment and therefore
    means ``X = true``.  Other prose such as "restores the lighthouse" is not
    interpreted here; semantic inference remains an audited Agent responsibility.
    """

    assignments: list[SourceFactAssignment] = []
    seen: set[str] = set()
    for match in _EXPLICIT_FACT_ASSIGNMENT.finditer(text):
        path = match.group("path").removeprefix("facts.")
        if path in seen:
            continue
        raw_value = (match.group("value") or "true").casefold()
        assignments.append(
            SourceFactAssignment(path=path, value=raw_value in {"true", "真"})
        )
        seen.add(path)
    return tuple(assignments)


__all__ = [
    "SourceFactAssignment",
    "SourceOutcomeClause",
    "action_goal_boundary_command",
    "extract_explicit_fact_assignments",
    "extract_source_outcome_clauses",
    "is_action_goal_boundary_command",
    "normalized_source_contains",
    "source_authorizes_push",
    "unspecified_outcome_summary",
]
