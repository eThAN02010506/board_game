"""Immutable extracted-source data passed from file adapters to application services."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExtractedRulebook:
    source_hash: str
    page_count: int
    title: str
    metadata: dict[str, Any]
    chunks: list[dict[str, Any]]
