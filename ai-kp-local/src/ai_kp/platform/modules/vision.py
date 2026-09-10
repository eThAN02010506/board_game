"""Provider-neutral derived analysis result for immutable module images."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleAssetAnalysis:
    ocr_text: str | None
    visual_summary: str | None
    model: str
    prompt_version: str
