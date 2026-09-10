"""Deterministic source-location to setting-template association."""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.platform.scenes.settlement_templates import SettlementSkeleton

_WORD = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.IGNORECASE)


class LocationMention(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    mention_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    context: str = Field(default="", max_length=2000)
    source_block_ids: tuple[str, ...] = Field(default=(), max_length=8)
    explicit_slot_id: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
    )


class TemplateMatchCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot_id: str
    score: float = Field(ge=0, le=1)
    matched_terms: tuple[str, ...]


class LocationTemplateBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mention_id: str
    mention_title: str
    status: str = Field(pattern="^(matched|ambiguous|unmatched)$")
    matched_slot_id: str | None = None
    candidates: tuple[TemplateMatchCandidate, ...] = Field(default=(), max_length=3)
    source_block_ids: tuple[str, ...] = ()


class SettlementTemplateCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bindings: tuple[LocationTemplateBinding, ...]
    covered_slot_ids: tuple[str, ...]
    missing_core_slot_ids: tuple[str, ...]
    suggested_typical_slot_ids: tuple[str, ...]
    conditional_slot_ids: tuple[str, ...]
    assumptions: tuple[str, ...]


def associate_location_mentions(
    skeleton: SettlementSkeleton,
    mentions: tuple[LocationMention, ...],
) -> SettlementTemplateCoverage:
    """Associate only high-margin matches; uncertainty remains explicit for KP review."""

    bindings = tuple(_bind_mention(skeleton, mention) for mention in mentions)
    covered = tuple(
        dict.fromkeys(
            item.matched_slot_id
            for item in bindings
            if item.status == "matched" and item.matched_slot_id is not None
        )
    )
    covered_set = set(covered)
    return SettlementTemplateCoverage(
        bindings=bindings,
        covered_slot_ids=covered,
        missing_core_slot_ids=tuple(
            item.slot_id
            for item in skeleton.scene_slots
            if item.frequency == "core" and item.slot_id not in covered_set
        ),
        suggested_typical_slot_ids=tuple(
            item.slot_id
            for item in skeleton.scene_slots
            if item.frequency == "typical" and item.slot_id not in covered_set
        ),
        conditional_slot_ids=tuple(
            item.slot_id
            for item in skeleton.scene_slots
            if item.frequency == "conditional" and item.slot_id not in covered_set
        ),
        assumptions=(
            "已提及地点只在高置信且明显领先其他候选时自动关联。",
            "缺失核心槽位表示功能覆盖候选，不证明必须存在独立建筑。",
        ),
    )


def _bind_mention(
    skeleton: SettlementSkeleton, mention: LocationMention
) -> LocationTemplateBinding:
    if mention.explicit_slot_id is not None:
        if not any(
            slot.slot_id == mention.explicit_slot_id for slot in skeleton.scene_slots
        ):
            raise ValueError(
                f"Explicit location binding references unknown slot: {mention.explicit_slot_id}"
            )
        candidate = TemplateMatchCandidate(
            slot_id=mention.explicit_slot_id,
            score=1.0,
            matched_terms=("kp_explicit_binding",),
        )
        return LocationTemplateBinding(
            mention_id=mention.mention_id,
            mention_title=mention.title,
            status="matched",
            matched_slot_id=mention.explicit_slot_id,
            candidates=(candidate,),
            source_block_ids=mention.source_block_ids,
        )
    query = f"{mention.title} {mention.context}".strip()
    candidates = sorted(
        (
            candidate
            for slot in skeleton.scene_slots
            if (
                candidate := _candidate(
                    query, slot.slot_id, slot.function, slot.building_candidates
                )
            )
            is not None
        ),
        key=lambda item: (-item.score, item.slot_id),
    )[:3]
    first = candidates[0] if candidates else None
    second_score = candidates[1].score if len(candidates) > 1 else 0.0
    matched = first is not None and first.score >= 0.58 and first.score - second_score >= 0.12
    status = "matched" if matched else "ambiguous" if first and first.score >= 0.35 else "unmatched"
    return LocationTemplateBinding(
        mention_id=mention.mention_id,
        mention_title=mention.title,
        status=status,
        matched_slot_id=first.slot_id if matched else None,
        candidates=tuple(candidates),
        source_block_ids=mention.source_block_ids,
    )


def _candidate(
    query: str,
    slot_id: str,
    function: str,
    variants: tuple[str, ...],
) -> TemplateMatchCandidate | None:
    normalized_query = _normalize(query)
    terms = (function, *variants)
    exact = tuple(
        term
        for term in terms
        if len(_normalize(term)) >= 2 and _normalize(term) in normalized_query
    )
    if exact:
        return TemplateMatchCandidate(slot_id=slot_id, score=1.0, matched_terms=exact)
    query_tokens = _tokens(query)
    scored = [
        (len(query_tokens & _tokens(term)) / max(1, len(_tokens(term))), term) for term in terms
    ]
    score = max((item[0] for item in scored), default=0.0)
    if score <= 0:
        return None
    return TemplateMatchCandidate(
        slot_id=slot_id,
        score=round(score, 4),
        matched_terms=tuple(term for value, term in scored if value == score),
    )


def _normalize(value: str) -> str:
    return "".join(_WORD.findall(unicodedata.normalize("NFKC", value).casefold()))


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    basic = _WORD.findall(normalized)
    cjk = "".join(item for item in basic if len(item) == 1 and "\u3400" <= item <= "\u9fff")
    return {
        *(item for item in basic if not (len(item) == 1 and "\u3400" <= item <= "\u9fff")),
        *(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1))),
    }


__all__ = [
    "LocationMention",
    "LocationTemplateBinding",
    "SettlementTemplateCoverage",
    "TemplateMatchCandidate",
    "associate_location_mentions",
]
