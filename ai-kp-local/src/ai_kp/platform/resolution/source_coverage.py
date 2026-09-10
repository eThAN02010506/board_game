"""Deterministic coverage obligations derived from extracted scenario evidence."""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.platform.resolution.check_catalog import ScenarioCheckCatalog
from ai_kp.platform.resolution.effect_catalog import ScenarioEffectCatalog

ScenarioRecordKind = Literal[
    "locations",
    "location_links",
    "entities",
    "clocks",
    "resources",
    "clues",
    "operators",
    "task_methods",
    "reactive_policies",
    "consequence_signals",
    "endings",
]


class SourceCoverageRequirement(BaseModel):
    """One server-owned claim that a source block needs executable representation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    requirement_key: str = Field(min_length=1, max_length=80)
    acceptable_record_kinds: tuple[ScenarioRecordKind, ...] = Field(min_length=1)
    minimum_record_count: int = Field(default=1, ge=1, le=16)
    blocking: bool = True
    reason: str = Field(min_length=1, max_length=300)


class SourceCoverageSupplementTarget(BaseModel):
    """Server-owned bounded request for records missing from an assembled candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source_block_id: str = Field(min_length=1, max_length=160)
    requirement_key: str = Field(min_length=1, max_length=80)
    acceptable_record_kinds: tuple[ScenarioRecordKind, ...] = Field(min_length=1)
    required_additional_count: int = Field(ge=1, le=16)
    blocking: bool = True
    reason: str = Field(min_length=1, max_length=300)


_BRACKETED_CHECK = re.compile(
    r"(?:《[^》《\n]{1,40}》|【[^【】\n]{1,40}】)"
    r"(?:技能)?(?:检定|判定|鉴定)",
    re.IGNORECASE,
)
_DIRECTED_CHECK = re.compile(
    r"(?:进行|要求|需要|可做|必须|应当|建议|让|通过).{0,32}?"
    r"(?:检定|判定|鉴定)",
    re.IGNORECASE,
)
_NO_CHECK = re.compile(
    r"(?:不(?:再)?需要|无需|不必|不要求).{0,16}?(?:检定|判定|鉴定)",
    re.IGNORECASE,
)
_EXPLICIT_ENDING_RULE = re.compile(
    r"(?:(?:如果|若|一旦|当(?!然)).{0,48}?(?:则|就|将|会|进入|触发|达成).{0,24}?"
    r"(?:结局|end)|(?:进入|触发|达成|迎来).{0,16}?(?:结局|end)|"
    r"(?:结局|ending).{0,8}?(?:条件|触发|达成))",
    re.IGNORECASE,
)
_SECTION_TERMINAL_OUTCOME = re.compile(
    r"(?:"
    r"(?:如果|若|一旦|当(?!然)).{0,120}?"
    r"(?:解决|解除|消除|击败|消灭|获胜|胜利|未能|失败|死亡|逃离|完成|终止|结束)"
    r"|\b(?:if|when|once)\b.{0,160}?\b(?:"
    r"resolv(?:e[sd]?|ing)|resolution|defeat(?:ed|s)?|destroy(?:ed|s)?|"
    r"win|wins|won|fail(?:ed|s)?|die[sd]?|dead|escap(?:e[sd]?|ing)|"
    r"get(?:s|ting)?\s+away|got\s+away|complet(?:e[sd]?|ing)|"
    r"conclud(?:e[sd]?|ing)|end(?:ed|s|ing)?"
    r")\b"
    r")",
    re.IGNORECASE,
)
_ZH_TERMINAL_OBSERVATION = re.compile(
    r"(?P<clause>(?:如果|若|一旦|当(?!然))[^，,；;。！？\n]{2,180}"
    r"(?:已经|已然|已|成功|均已|"
    r"(?:全部|全都|均)[^，,；;。！？\n]{0,30}(?:死亡|逃离|脱身|完成)|"
    r"(?:解决|解除|消除|击败|消灭|逃离|脱身|完成|结束)(?:了|完)|"
    r"死亡|耗尽|归零|达到)"
    r"[^，,；;。！？\n]{0,100})(?=[，,；;。！？])",
    re.IGNORECASE,
)
_EN_TERMINAL_OBSERVATION = re.compile(
    r"\b(?P<clause>(?:if|when|once)\s+[^,;.!?\n]{2,180}?\b(?:"
    r"manage(?:d|s)?\s+to|successfully|got\s+away|"
    r"(?:have|has|had)\s+[^,;.!?\n]{0,50}\b(?:escaped|got(?:ten)?\s+away|"
    r"completed|resolved|defeated|destroyed|died)|"
    r"(?:is|are|was|were)\s+(?:dead|defeated|destroyed|safe|free|gone|"
    r"complete|resolved)|escaped|completed|resolved|defeated|destroyed|"
    r"died|expires?|expired|reaches?|reached"
    r")\b[^,;.!?\n]{0,100})(?=[,;.!?])",
    re.IGNORECASE,
)
_TERMINAL_INTENT_ONLY = re.compile(
    r"(?:想要?|希望|准备|试图|尝试).{0,32}(?:逃离|脱身|完成)|"
    r"\b(?:want(?:s|ed)?|wish(?:es|ed)?|hope(?:s|d)?|plan(?:s|ned)?|"
    r"try|tries|tried|attempt(?:s|ed)?)\s+to\b",
    re.IGNORECASE,
)
_PRESSURE = re.compile(
    r"(?:每(?:隔|当|次)?\s*[一二三四五六七八九十百0-9]+?.{0,5}(?:轮|回合|分钟|小时)|"
    r"倒计时|截止时间|(?:时间限制|限时|时限)(?:为|是|：|:|在)?\s*"
    r"[一二三四五六七八九十百0-9]+|第[一二三四五六七八九十百0-9]+(?:轮|回合|分钟|小时))",
    re.IGNORECASE,
)
_SEMANTIC_REQUIREMENTS: dict[
    str, tuple[str, tuple[ScenarioRecordKind, ...], float, str]
] = {
    "scene": (
        "scene_structure",
        ("locations", "operators"),
        0.65,
        "高置信场景块应形成地点或可执行行动。",
    ),
    "clue": (
        "clue_path",
        ("clues", "operators"),
        0.65,
        "明确线索块应形成线索或发现行动。",
    ),
    "npc": (
        "npc_presence",
        ("entities", "operators", "reactive_policies"),
        0.7,
        "高置信 NPC 块应形成实体、交互或反应策略。",
    ),
    "stat_block": (
        "stat_block",
        ("entities", "operators"),
        0.8,
        "高置信数据块应绑定实体或相关行动。",
    ),
    "handout": (
        "handout_content",
        ("clues", "entities", "operators"),
        0.8,
        "明确玩家资料应形成线索、实体或读取行动。",
    ),
    "ending": (
        "ending_rule",
        ("endings",),
        0.7,
        "明确结局块应形成可判定结局规则。",
    ),
    "optional_branch": (
        "optional_branch",
        ("operators", "task_methods", "endings"),
        0.75,
        "明确可选分支应形成行动、计划或结局。",
    ),
}


_SENTENCE_BOUNDARY = re.compile(r"[.!?。！？；;]")
_CONTENT_CHARACTER = re.compile(r"[\w\u3400-\u9fff]", re.UNICODE)
_ALTERNATIVE_CHECK_CONNECTOR = re.compile(
    r"(?:或者|或|/|\bor\b)", re.IGNORECASE
)
_SEQUENTIAL_CHECK_CONNECTOR = re.compile(
    r"(?:失败|成功|随后|然后|接着|之后|\b(?:then|after|failure|success)\b)",
    re.IGNORECASE,
)
_CHARACTER_SHEET_MARKER = re.compile(
    r"(?:major\s+wound|temp\.?\s*insane|indef\.?\s*insane|max\s+insane|"
    r"dying|characteristics)",
    re.IGNORECASE,
)
_NUMBER_TRACK = re.compile(r"(?:\b\d{1,2}\b[\s,]*){12,}")


def _reference_only_effect_data(semantic_kind: str, text: str) -> bool:
    """Keep sheets and entity attributes out of executable action coverage."""

    if semantic_kind == "stat_block":
        return True
    if semantic_kind != "table":
        return False
    return bool(
        len(_CHARACTER_SHEET_MARKER.findall(text)) >= 2
        or _NUMBER_TRACK.search(text)
    )


def _has_substantive_semantic_content(text: str) -> bool:
    """Reject labels/headings without assuming any scenario-specific vocabulary.

    Extractors occasionally classify a title as a scene, clue, or NPC paragraph.
    A short label can name a thing but cannot entail an executable record. Longer
    blocks and complete sentences remain eligible for semantic materialization;
    explicit mechanics below are detected independently and are never weakened by
    this presentation-level guard.
    """

    compact = "".join(_CONTENT_CHARACTER.findall(text))
    if len(compact) < 12:
        return False
    return len(compact) >= 48 or bool(_SENTENCE_BOUNDARY.search(text)) or "\n" in text.strip()


def extract_terminal_observation_clauses(text: str) -> tuple[str, ...]:
    """Return source clauses that assert an already observable terminal state.

    Wishes and proposed actions are deliberately excluded: a terminal confirmation
    may acknowledge an outcome the table can already observe, never make an attempt
    succeed by declaration.
    """

    clauses = tuple(
        clause[:240]
        for pattern in (_ZH_TERMINAL_OBSERVATION, _EN_TERMINAL_OBSERVATION)
        for match in pattern.finditer(text)
        if not _TERMINAL_INTENT_ONLY.search(
            clause := " ".join(match.group("clause").split())
        )
    )
    return tuple(dict.fromkeys(clauses))[:8]


def _minimum_check_record_count(
    text: str, spans: list[tuple[int, int]]
) -> int:
    """Count executable check steps, merging alternatives within one step."""

    count = len(spans)
    for previous, current in pairwise(spans):
        connector = text[previous[1] : current[0]]
        if (
            _ALTERNATIVE_CHECK_CONNECTOR.search(connector)
            and not _SEQUENTIAL_CHECK_CONNECTOR.search(connector)
        ):
            count -= 1
    return max(count, 1)


def infer_source_coverage_requirements(
    *,
    semantic_kind: str,
    classification_confidence: float,
    text: str,
    check_catalog: ScenarioCheckCatalog | None = None,
    effect_catalog: ScenarioEffectCatalog | None = None,
) -> tuple[SourceCoverageRequirement, ...]:
    """Derive conservative obligations without asking a model what it omitted."""

    requirements: list[SourceCoverageRequirement] = []
    semantic = _SEMANTIC_REQUIREMENTS.get(semantic_kind)
    if semantic_kind == "ending" and not (
        _EXPLICIT_ENDING_RULE.search(text) or _SECTION_TERMINAL_OUTCOME.search(text)
    ):
        semantic = None
    if semantic is not None and _has_substantive_semantic_content(text):
        key, kinds, threshold, reason = semantic
        if classification_confidence >= threshold:
            requirements.append(
                SourceCoverageRequirement(
                    requirement_key=key,
                    acceptable_record_kinds=kinds,
                    # A source-explicit terminal condition is a hard mechanic;
                    # descriptive world materialization remains best-effort.
                    blocking=semantic_kind == "ending",
                    reason=reason,
                )
            )
            observation_clauses = (
                extract_terminal_observation_clauses(text)
                if key == "ending_rule"
                else ()
            )
            if observation_clauses:
                requirements.append(
                    SourceCoverageRequirement(
                        requirement_key="explicit_terminal_observation",
                        acceptable_record_kinds=("operators",),
                        minimum_record_count=len(observation_clauses),
                        blocking=True,
                        reason=(
                            "来源结局条件包含已完成且玩家可观察的状态；应先形成"
                            "显式确认行动，再由结局引用其权威结果。"
                        ),
                    )
                )

    directed_spans = {
        match.span()
        for match in _DIRECTED_CHECK.finditer(text)
        if not _NO_CHECK.search(text[max(0, match.start() - 4) : match.end()])
    }
    bracketed_spans = {
        match.span()
        for match in _BRACKETED_CHECK.finditer(text)
    }
    raw_check_spans = bracketed_spans if directed_spans and bracketed_spans else directed_spans
    check_spans: list[tuple[int, int]] = []
    for start, end in sorted(raw_check_spans):
        if check_spans and start < check_spans[-1][1]:
            previous_start, previous_end = check_spans[-1]
            check_spans[-1] = (previous_start, max(previous_end, end))
        else:
            check_spans.append((start, end))
    source_checks = (
        check_catalog.extract_source_checks(text) if check_catalog is not None else ()
    )
    if check_spans:
        requirements.append(
            SourceCoverageRequirement(
                requirement_key="explicit_checks",
                acceptable_record_kinds=("operators",),
                minimum_record_count=min(
                    _minimum_check_record_count(text, check_spans), 8
                ),
                # A directed but unnamed check can be a section lead-in, rules
                # explanation, or extraction fragment whose concrete skill is in
                # another block. Keep it visible for materialization, but only a
                # locally named check is safe to make an auto-publish blocker.
                blocking=bool(bracketed_spans or source_checks),
                reason="来源明确要求检定；每项应由带来源的行动表示。",
            )
        )

    source_effects = (
        effect_catalog.extract_source_effects(text)
        if effect_catalog is not None
        and not _reference_only_effect_data(semantic_kind, text)
        else ()
    )
    if source_effects:
        requirements.append(
            SourceCoverageRequirement(
                requirement_key="explicit_ruleset_effect",
                acceptable_record_kinds=("operators",),
                reason=(
                    "来源明确给出已安装规则插件可解析的后果；"
                    "应形成目录允许的角色规则效果。"
                ),
            )
        )

    # Import lazily: the materializer consumes SourceCoverageSupplementTarget,
    # while requirement inference only needs its pure source parser.
    from ai_kp.platform.resolution.scenario_terminal_method import (
        extract_source_terminal_methods,
    )

    terminal_method_count = len(extract_source_terminal_methods(text))
    if terminal_method_count:
        requirements.append(
            SourceCoverageRequirement(
                requirement_key="explicit_terminal_method",
                acceptable_record_kinds=("operators",),
                minimum_record_count=terminal_method_count,
                blocking=True,
                reason=(
                    "来源明确给出玩家手段导致实体终结；应形成只引用既有实体、"
                    "由服务器生成状态命令的可执行行动。"
                ),
            )
        )

    pressure_count = len(tuple(_PRESSURE.finditer(text)))
    if pressure_count:
        requirements.append(
            SourceCoverageRequirement(
                requirement_key="explicit_pressure",
                acceptable_record_kinds=(
                    "clocks",
                    "operators",
                    "reactive_policies",
                    "consequence_signals",
                ),
                minimum_record_count=min(pressure_count, 8),
                reason="来源明确给出时间或轮次压力；应形成时钟、行动或反应。",
            )
        )
    return tuple(requirements)


__all__ = [
    "ScenarioRecordKind",
    "SourceCoverageRequirement",
    "SourceCoverageSupplementTarget",
    "extract_terminal_observation_clauses",
    "infer_source_coverage_requirements",
]
