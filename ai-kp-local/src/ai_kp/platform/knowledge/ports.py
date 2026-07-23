"""Technology-neutral rulebook extraction and original-text index ports."""

from collections.abc import Callable
from typing import Any, Protocol

from ai_kp.platform.knowledge.sources import ExtractedRulebook


class OriginalTextIndex(Protocol):
    async def index_chunks(self, chunks: list[dict[str, Any]]) -> dict[str, str]: ...

    async def retrieve(self, query: str, top_k: int = 8) -> list[str]: ...


RulebookExtractor = Callable[[bytes, str], ExtractedRulebook]
OriginalTextIndexFactory = Callable[[str, str], OriginalTextIndex]


class OriginalTextIndexUnavailableError(RuntimeError):
    """Raised when the optional semantic-index implementation is unavailable."""


__all__ = [
    "OriginalTextIndex",
    "OriginalTextIndexFactory",
    "OriginalTextIndexUnavailableError",
    "RulebookExtractor",
]
