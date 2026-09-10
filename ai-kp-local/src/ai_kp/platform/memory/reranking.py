"""Ruleset-neutral semantic reranking boundary for authorized memories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MemoryRerankCandidate:
    """A bounded, already-authorized candidate exposed to a local reranker."""

    memory_id: str
    text: str
    scope: str
    importance: int


class MemoryRerankUnavailable(RuntimeError):
    """A recoverable local reranker/model availability failure."""


class MemorySemanticRanker(Protocol):
    """Return candidate IDs from most to least relevant.

    Implementations may use embeddings, a cross-encoder, or a constrained Agent.
    They receive no campaign/player identifiers and cannot grant visibility or
    mutate state. Unknown and duplicate IDs are ignored by the caller. An
    implementation that cannot complete should raise ``MemoryRerankUnavailable``
    so retrieval can fall back to its deterministic first-stage ranks.
    """

    def rank(
        self,
        query: str,
        candidates: tuple[MemoryRerankCandidate, ...],
    ) -> tuple[str, ...]: ...


__all__ = [
    "MemoryRerankCandidate",
    "MemoryRerankUnavailable",
    "MemorySemanticRanker",
]
