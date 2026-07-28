"""Provider-neutral derived analysis result for immutable module images."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModuleAssetAnalysis:
    ocr_text: str | None
    visual_summary: str | None
    model: str
    prompt_version: str


class ModuleVisionAnalyzer(Protocol):
    async def analyze(
        self,
        data: bytes,
        *,
        mime_type: str,
        source_locator: str,
    ) -> ModuleAssetAnalysis: ...
