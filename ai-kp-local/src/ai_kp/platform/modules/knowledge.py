"""Source-bound module knowledge candidates and deterministic citation validation."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

KnowledgeKind = Literal["module_canon", "module_anchor", "reference"]
Visibility = Literal["player", "table", "kp", "secret"]
VISIBILITY_RANK: dict[str, int] = {
    "player": 0,
    "table": 0,
    "kp": 1,
    "secret": 2,
}


def validate_derived_scope(
    *,
    source_visibility: str,
    source_spoiler_tag: str | None,
    derived_visibility: str,
    derived_spoiler_tag: str | None,
    label: str,
) -> None:
    """Reject knowledge projections that weaken their source security scope."""

    if VISIBILITY_RANK[derived_visibility] < VISIBILITY_RANK[source_visibility]:
        raise ValueError(f"{label} visibility cannot be wider than its source")
    if source_spoiler_tag and derived_spoiler_tag != source_spoiler_tag:
        raise ValueError(f"{label} must preserve its source spoiler tag")


class ModuleCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str | None = None
    asset_id: str | None = None
    evidence_text: str = Field(min_length=2, max_length=1000)
    evidence_hash: str = ""
    source_locator: str = ""

    @model_validator(mode="after")
    def exactly_one_source(self) -> ModuleCitation:
        if (self.chunk_id is None) == (self.asset_id is None):
            raise ValueError("Citation requires exactly one chunk_id or asset_id")
        return self


class ModuleKnowledgeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: KnowledgeKind
    title: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=2, max_length=4000)
    rationale: str = Field(default="", max_length=2000)
    confidence: float = Field(default=0, ge=0, le=1)
    visibility: Visibility = "kp"
    spoiler_tag: str | None = Field(default=None, max_length=160)
    citations: list[ModuleCitation] = Field(min_length=1, max_length=8)


class ModuleKnowledgeValidationStore(Protocol):
    def get_module_chunk(self, chunk_id: str) -> dict: ...

    def get_module_asset(self, asset_id: str) -> dict: ...


def normalize_evidence(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def validate_module_candidate(
    repo: ModuleKnowledgeValidationStore,
    module_id: str,
    payload: dict[str, Any],
) -> tuple[ModuleKnowledgeCandidate, str]:
    candidate = ModuleKnowledgeCandidate.model_validate(payload)
    for citation in candidate.citations:
        if citation.chunk_id:
            source = repo.get_module_chunk(citation.chunk_id)
            source_text = str(source["text"])
        else:
            source = repo.get_module_asset(str(citation.asset_id))
            source_text = "\n".join(
                str(source.get(key) or "")
                for key in ("ocr_text", "visual_summary")
            )
        if source["module_id"] != module_id:
            raise ValueError("Citation belongs to another module")
        validate_derived_scope(
            source_visibility=str(source.get("visibility") or "kp"),
            source_spoiler_tag=source.get("spoiler_tag"),
            derived_visibility=candidate.visibility,
            derived_spoiler_tag=candidate.spoiler_tag,
            label="Candidate",
        )
        if normalize_evidence(citation.evidence_text) not in normalize_evidence(source_text):
            raise ValueError("Citation evidence is not present in its source")
        citation.source_locator = str(source.get("source_locator") or "")
        citation.evidence_hash = hashlib.sha256(
            citation.evidence_text.strip().encode("utf-8")
        ).hexdigest()

    canonical = json.dumps(
        candidate.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return candidate, hashlib.sha256(canonical.encode("utf-8")).hexdigest()
