"""Closed source slots for explicit methods that terminally affect an entity.

The source parser owns the method and terminal status.  A weak model may only
select a parsed method slot and an already-authored entity slot; executable
identity, provenance, preconditions, and commands remain server-owned.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_kp.platform.resolution.contracts import (
    EntitySpec,
    OutcomeNarrativeCue,
    ScenarioContract,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.scenario_ir_models import IrAction, ScenarioIrBatch
from ai_kp.platform.resolution.source_coverage import SourceCoverageSupplementTarget

TerminalStatus = Literal["defeated", "destroyed", "dead", "incapacitated"]
_TERMINAL_METHOD_FACT = re.compile(
    r"^source_terminal_methods\.[0-9a-f]{24}\.achieved$"
)
_BODY_MANIFESTATION_KINDS = ("躯体", "尸体", "身体", "遗体", "肉身")
_BODY_MANIFESTATION_ALTERNATION = "|".join(_BODY_MANIFESTATION_KINDS)
_BODY_OUTCOME_SUBJECT = re.compile(
    rf"^(?P<base>[\w\u3400-\u9fff·.-]{{2,80}}?)"
    rf"的(?P<kind>{_BODY_MANIFESTATION_ALTERNATION})"
    r"(?=(?:会|将|就|被|立刻|马上|最终|立即|"
    r"化为|化成|变成|死亡|死去|毙命|失去行动能力|无法继续行动))",
    re.IGNORECASE,
)
_BODY_ENTITY_TITLE = re.compile(
    rf"^(?P<base>[\w\u3400-\u9fff·.-]{{2,80}}?)(?:的|·)"
    rf"(?P<kind>{_BODY_MANIFESTATION_ALTERNATION})$|"
    rf"^(?P<parenthesized_base>[\w\u3400-\u9fff·.-]{{2,80}}?)"
    rf"[（(](?P<parenthesized_kind>{_BODY_MANIFESTATION_ALTERNATION})[）)]$",
    re.IGNORECASE,
)


class SourceTerminalMethod(BaseModel):
    """One conditional terminal transition parsed from a source sentence."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source_slot: int = Field(ge=0, le=7)
    title: str = Field(min_length=1, max_length=240)
    outcome_summary: str = Field(min_length=1, max_length=500)
    terminal_status: TerminalStatus
    outcome_subject_aliases: tuple[str, ...] = Field(default=(), max_length=4)


class TerminalEntityCandidate(BaseModel):
    """Opaque existing-entity slot that the model may select."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    entity_slot: int = Field(ge=0, le=15)
    entity_id: str = Field(min_length=1, max_length=160, exclude=True)
    title: str = Field(min_length=1, max_length=240)
    source_alias: str = Field(min_length=2, max_length=240, exclude=True)
    initial_status: str = Field(min_length=1, max_length=120, exclude=True)
    initial_location_id: str = Field(min_length=1, max_length=160, exclude=True)


class TerminalMethodSelection(BaseModel):
    """The entire weak-model authority surface: two server-owned slot numbers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_method_slot: int = Field(ge=0, le=7)
    target_entity_slot: int = Field(ge=0, le=15)


class TerminalMethodEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selections: tuple[TerminalMethodSelection, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def reject_duplicate_selections(self) -> TerminalMethodEnvelope:
        identities = tuple(
            (item.source_method_slot, item.target_entity_slot)
            for item in self.selections
        )
        if len(identities) != len(set(identities)):
            raise ValueError("Terminal method selections must be unique")
        return self


_TERMINAL_METHOD_SCHEMA_JSON = json.dumps(
    TerminalMethodEnvelope.model_json_schema(),
    ensure_ascii=False,
    separators=(",", ":"),
)

_ZH_CONDITIONAL = re.compile(
    r"(?:如果|若(?:是)?|一旦|当)(?P<condition>[^。！？；;\n]{2,300}?)"
    r"(?:，|,)?\s*(?:则|那么|就)(?P<outcome>[^。！？；;\n]{2,220})",
    re.IGNORECASE,
)
_EN_CONDITIONAL = re.compile(
    r"\b(?:if|when|once)\s+(?P<condition>[^.!?;\n]{2,300}?)"
    r"\s*,?\s*(?:then\s+)?(?P<outcome>[^.!?;\n]{2,220})",
    re.IGNORECASE,
)
_AGENTIVE_METHOD = re.compile(
    r"(?:调查员|玩家|角色|猎人|英雄|队伍).{0,100}?"
    r"(?:成功|用|使用|以|反过来|攻击|刺|杀|斩|焚|烧|摧毁|破坏|解除|关闭|砸|射)|"
    r"\b(?:investigators?|players?|characters?|party|heroes?|hunters?|someone)\b"
    r".{0,120}?\b(?:successfully|use[sd]?|attack(?:ed|s)?|stab(?:bed|s)?|kill(?:ed|s)?|"
    r"destroy(?:ed|s)?|burn(?:ed|s)?|break(?:s|ing|broke)?|sever(?:ed|s)?)\b",
    re.IGNORECASE,
)
_TERMINAL_MARKERS: tuple[tuple[TerminalStatus, re.Pattern[str]], ...] = (
    (
        "destroyed",
        re.compile(
            r"(?:化为|化成|变成).{0,8}?(?:尘埃|灰烬)|彻底(?:消灭|摧毁)|"
            r"\b(?:turn(?:s|ed)?\s+to\s+(?:dust|ash)|disintegrat(?:e[sd]?|ed|ion)|"
            r"utterly\s+destroyed)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "defeated",
        re.compile(r"(?:被)?击败|战胜|\b(?:is\s+)?defeat(?:ed|s)?\b", re.IGNORECASE),
    ),
    (
        "dead",
        re.compile(r"(?:死亡|死去|毙命|被杀死)|\b(?:dies?|dead|is\s+killed)\b", re.IGNORECASE),
    ),
    (
        "incapacitated",
        re.compile(
            r"(?:失去行动能力|无法继续行动)|\b(?:incapacitat(?:e[sd]?|ed)|unable\s+to\s+act)\b",
            re.IGNORECASE,
        ),
    ),
)


def coverage_terminal_method_schema_json() -> str:
    return _TERMINAL_METHOD_SCHEMA_JSON


def extract_source_terminal_methods(text: str) -> tuple[SourceTerminalMethod, ...]:
    """Extract only agentive conditional causes with an explicit terminal result."""

    extracted: list[SourceTerminalMethod] = []
    for match in (*_ZH_CONDITIONAL.finditer(text), *_EN_CONDITIONAL.finditer(text)):
        condition = " ".join(match.group("condition").split())
        outcome = " ".join(match.group("outcome").split())
        if not _AGENTIVE_METHOD.search(condition):
            continue
        terminal_match = next(
            (
                (status, matched)
                for status, marker in _TERMINAL_MARKERS
                if (matched := marker.search(outcome)) is not None
            ),
            None,
        )
        if terminal_match is None:
            continue
        status, matched = terminal_match
        outcome_subject_aliases = _terminal_outcome_subject_aliases(
            outcome[: matched.start()]
        )
        title = condition[:240]
        signature = (title.casefold(), outcome.casefold(), status)
        if any(
            (item.title.casefold(), item.outcome_summary.casefold(), item.terminal_status)
            == signature
            for item in extracted
        ):
            continue
        extracted.append(
            SourceTerminalMethod(
                source_slot=len(extracted),
                title=title,
                outcome_summary=outcome[:500],
                terminal_status=status,
                outcome_subject_aliases=outcome_subject_aliases,
            )
        )
        if len(extracted) >= 8:
            break
    return tuple(extracted)


def _terminal_outcome_subject_aliases(prefix: str) -> tuple[str, ...]:
    """Recover only an exact grammatical subject immediately before the result."""

    compact = prefix.strip(" ，,、:：-—")
    zh = re.fullmatch(
        r"(?P<name>[\w\u3400-\u9fff·.-]{2,80}?)"
        r"(?:的(?:躯体|身体|肉身|灵体|尸体|遗体))?"
        r"(?:会|将|就|被|立刻|马上|最终|立即)*",
        compact,
        re.IGNORECASE,
    )
    if zh is not None:
        name = zh.group("name").strip()
        return (name,) if len(name) >= 2 else ()
    english = re.fullmatch(
        r"(?:the\s+)?(?P<name>[a-z][a-z0-9 .'-]{1,78}?)"
        r"\s+(?:will|would|is|are|immediately|instantly)*\s*",
        compact,
        re.IGNORECASE,
    )
    if english is None:
        return ()
    name = english.group("name").strip()
    return (name,) if len(name) >= 2 else ()


def supports_terminal_method_materialization(
    targets: tuple[SourceCoverageSupplementTarget, ...],
) -> bool:
    return bool(targets) and all(
        target.requirement_key == "explicit_terminal_method"
        and target.acceptable_record_kinds == ("operators",)
        for target in targets
    )


def build_terminal_entity_candidates(
    contract: ScenarioContract,
    source_text: str,
) -> tuple[TerminalEntityCandidate, ...]:
    """Advertise only source-exact aliases that identify one existing entity.

    Punctuation/interpunct/parentheses and whitespace may delimit title tokens,
    but tokens are never stemmed or fuzzily compared.  An alias shared by two
    entities is withheld, including duplicate full titles.
    """

    def aliases(title: str) -> tuple[str, ...]:
        tokens = tuple(
            token
            for token in re.split(r"[\s\W_]+", title, flags=re.UNICODE)
            if len(token) >= 2
        )
        return tuple(dict.fromkeys((title, *tokens)))

    aliases_by_entity = {
        entity.entity_id: aliases(entity.title) for entity in contract.entities
    }
    alias_owners: dict[str, set[str]] = {}
    for entity_id, entity_aliases in aliases_by_entity.items():
        for alias in entity_aliases:
            alias_owners.setdefault(alias.casefold(), set()).add(entity_id)
    terminal_methods = extract_source_terminal_methods(source_text)
    body_subjects = _body_outcome_subjects(terminal_methods)
    body_matches: dict[str, list[EntitySpec]] = {
        base: [] for base in body_subjects
    }
    for entity in contract.entities:
        if entity.initial_location_id is None:
            continue
        manifestation = _body_entity_manifestation(entity.title)
        if manifestation is None:
            continue
        base, _kind = manifestation
        folded_base = base.casefold()
        if folded_base in body_matches:
            body_matches[folded_base].append(entity)

    # An explicit body subject reserves its exact person base.  One located
    # body manifestation wins; multiple body records remain ambiguous; only
    # when none exists may an entity whose complete title is exactly the base
    # retain the old direct-title behavior.
    reserved_body_bases = {
        base for base, entities in body_matches.items() if entities
    }
    matched_by_entity: dict[str, tuple[EntitySpec, str]] = {}
    for base, entities in body_matches.items():
        if len(entities) != 1:
            continue
        entity = entities[0]
        matched_by_entity[str(entity.entity_id)] = (
            entity,
            body_subjects[base],
        )

    # Once a terminal conditional is available, target identity must come from
    # its outcome rather than from tools or bystanders named in the condition.
    # The source-wide behavior remains only for the low-level catalog API used
    # when no method has yet been parsed.
    candidate_text = (
        "\n".join(method.outcome_summary for method in terminal_methods)
        if terminal_methods
        else source_text
    )
    folded_source = candidate_text.casefold()
    for entity in contract.entities:
        if entity.initial_location_id is None:
            continue
        source_aliases = tuple(
            alias
            for alias in aliases_by_entity[entity.entity_id]
            if alias.casefold() in folded_source
            and alias_owners[alias.casefold()] == {entity.entity_id}
            and _direct_alias_allowed_for_body_subject(
                entity.title,
                alias,
                body_subjects=body_subjects,
                reserved_body_bases=reserved_body_bases,
            )
        )
        if source_aliases:
            matched_by_entity.setdefault(
                entity.entity_id,
                (entity, max(source_aliases, key=len)),
            )
    matched = [
        matched_by_entity[entity.entity_id]
        for entity in contract.entities
        if entity.entity_id in matched_by_entity
    ]
    return tuple(
        TerminalEntityCandidate(
            entity_slot=index,
            entity_id=entity.entity_id,
            title=entity.title,
            source_alias=source_alias,
            initial_status=entity.initial_status,
            initial_location_id=str(entity.initial_location_id),
        )
        for index, (entity, source_alias) in enumerate(matched[:16])
    )


def _body_outcome_subjects(
    methods: tuple[SourceTerminalMethod, ...],
) -> dict[str, str]:
    """Map exact person bases to the exact body phrase used by an outcome."""

    subjects: dict[str, str] = {}
    ambiguous: set[str] = set()
    for method in methods:
        matched = _BODY_OUTCOME_SUBJECT.match(method.outcome_summary)
        if matched is None:
            continue
        base = matched.group("base").strip()
        phrase = matched.group(0)
        folded_base = base.casefold()
        existing = subjects.get(folded_base)
        if existing is not None and existing.casefold() != phrase.casefold():
            ambiguous.add(folded_base)
            continue
        subjects[folded_base] = phrase
    for base in ambiguous:
        subjects.pop(base, None)
    return subjects


def _body_entity_manifestation(title: str) -> tuple[str, str] | None:
    matched = _BODY_ENTITY_TITLE.fullmatch(title.strip())
    if matched is None:
        return None
    base = matched.group("base") or matched.group("parenthesized_base")
    kind = matched.group("kind") or matched.group("parenthesized_kind")
    return base.strip(), kind


def _direct_alias_allowed_for_body_subject(
    entity_title: str,
    alias: str,
    *,
    body_subjects: dict[str, str],
    reserved_body_bases: set[str],
) -> bool:
    folded_alias = alias.casefold()
    if folded_alias not in body_subjects:
        return True
    if folded_alias in reserved_body_bases:
        return False
    return entity_title.casefold() == folded_alias


def materialize_terminal_method_envelope(
    envelope: TerminalMethodEnvelope,
    *,
    methods: tuple[SourceTerminalMethod, ...],
    entity_candidates: tuple[TerminalEntityCandidate, ...],
    source_block_id: str,
    record_id_prefix: str,
) -> ScenarioIrBatch:
    """Turn selected slots into causal actions without accepting model commands."""

    method_by_slot = {item.source_slot: item for item in methods}
    entity_by_slot = {item.entity_slot: item for item in entity_candidates}
    actions: list[IrAction] = []
    selected_methods: set[int] = set()
    for index, selection in enumerate(envelope.selections, start=1):
        method = method_by_slot.get(selection.source_method_slot)
        entity = entity_by_slot.get(selection.target_entity_slot)
        if method is None or entity is None:
            raise ValueError("Terminal method selection is outside the server catalog")
        if method.source_slot in selected_methods:
            raise ValueError("Each source terminal method may be materialized once")
        if entity.source_alias.casefold() not in method.outcome_summary.casefold():
            raise ValueError(
                "Selected terminal entity title is not named in the source outcome"
            )
        selected_methods.add(method.source_slot)
        action_id = f"{record_id_prefix}terminal_{index:02d}"
        actions.append(
            IrAction(
                id=action_id,
                title=method.title,
                intent_hints=tuple(
                    dict.fromkeys(
                        (
                            method.title[:160],
                            entity.source_alias[:160],
                            *(item[:160] for item in method.outcome_subject_aliases),
                        )
                    )
                )[:12],
                public_setup="The terminal method enters resolution.",
                narrative_cues=(
                    OutcomeNarrativeCue(
                        outcome_key="success",
                        public_summary=method.outcome_summary,
                    ),
                ),
                # The source names a complete player method but no ruleset skill.
                # Treat choosing that method as an automatic primitive; inventing
                # a check would cross the ruleset/source authority boundary.
                policy="automatic",
                location_id=entity.initial_location_id,
                preconditions=(
                    StateCondition(
                        path=f"entities.{entity.entity_id}",
                        operator="eq",
                        value=entity.initial_status,
                    ),
                ),
                on_success=(
                    WorldCommand(
                        kind="set_entity_status",
                        entity_id=entity.entity_id,
                        value=method.terminal_status,
                    ),
                ),
                source_block_ids=(source_block_id,),
            )
        )
    return ScenarioIrBatch(confidence="high", actions=tuple(actions))


def terminal_method_fact_path(
    *, source_block_id: str, method: SourceTerminalMethod
) -> str:
    identity = json.dumps(
        {
            "source_block_id": source_block_id,
            "source_slot": method.source_slot,
            "title": method.title,
            "outcome_summary": method.outcome_summary,
            "terminal_status": method.terminal_status,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"source_terminal_methods.{digest}.achieved"


def is_terminal_method_fact_command(command: WorldCommand) -> bool:
    return bool(
        command.kind == "set_fact"
        and command.path is not None
        and _TERMINAL_METHOD_FACT.fullmatch(command.path)
        and command.value is True
    )


def materialize_terminal_method_fallback(
    *,
    methods: tuple[SourceTerminalMethod, ...],
    source_block_id: str,
    record_id_prefix: str,
) -> ScenarioIrBatch:
    """Represent a source method without claiming which ambiguous entity changed."""

    actions = tuple(
        IrAction(
            id=f"{record_id_prefix}terminal_{index:02d}",
            title=method.title,
            intent_hints=tuple(
                dict.fromkeys(
                    (
                        method.title[:160],
                        *(item[:160] for item in method.outcome_subject_aliases),
                    )
                )
            )[:12],
            public_setup="The terminal method enters resolution.",
            narrative_cues=(
                OutcomeNarrativeCue(
                    outcome_key="success",
                    public_summary=method.outcome_summary,
                ),
            ),
            policy="automatic",
            on_success=(
                WorldCommand(
                    kind="set_fact",
                    path=terminal_method_fact_path(
                        source_block_id=source_block_id,
                        method=method,
                    ),
                    value=True,
                ),
            ),
            source_block_ids=(source_block_id,),
        )
        for index, method in enumerate(methods, start=1)
    )
    return ScenarioIrBatch(confidence="high", actions=actions)


__all__ = [
    "SourceTerminalMethod",
    "TerminalEntityCandidate",
    "TerminalMethodEnvelope",
    "build_terminal_entity_candidates",
    "coverage_terminal_method_schema_json",
    "extract_source_terminal_methods",
    "is_terminal_method_fact_command",
    "materialize_terminal_method_envelope",
    "materialize_terminal_method_fallback",
    "supports_terminal_method_materialization",
    "terminal_method_fact_path",
]
