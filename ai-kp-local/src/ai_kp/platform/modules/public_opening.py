"""Deterministically project explicitly player-facing scenario openings.

Module text is Keeper-private by default. This module only crosses that boundary
when the source itself contains a conventional read-aloud instruction and the
following document structure marks the prose as quoted/boxed text.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_READ_ALOUD = re.compile(
    r"(?:大声)?朗读(?:出)?(?:以下|下列)(?:内容|文字)?|"
    r"大声读出以下内容|"
    r"read\s+(?:the\s+following|this)\s+(?:aloud|to\s+the\s+players)|"
    r"boxed\s+text",
    re.IGNORECASE,
)
_ROLE_ONLY = re.compile(
    r"(?:给\s*(?:守密人|守秘人|主持人|kp)\s*的?\s*(?:提示|建议)|"
    r"(?:keeper|game\s*master|主持人|守密人|守秘人|\bkp\b)\s*"
    r"(?:应当|应该|可以|必须|需要|注意|提示|建议|should|must|may|note|tip))",
    re.IGNORECASE,
)
_STRUCTURAL_KINDS = {"heading", "scene"}
_PLAYER_PROSE_KINDS = {"text", "handout"}


def extract_public_opening(
    chunks: Sequence[Mapping[str, Any]],
    *,
    scan_limit: int = 10,
    character_limit: int = 2_400,
) -> str | None:
    """Return the first explicitly authorized opening, otherwise fail closed."""

    ordered = sorted(
        chunks,
        key=lambda item: (int(item.get("order_index") or 0), str(item.get("id") or "")),
    )
    for anchor_index, anchor in enumerate(ordered):
        instruction = str(anchor.get("text") or "").strip()
        if not _READ_ALOUD.search(instruction):
            continue

        paragraphs: list[str] = []
        for item in ordered[anchor_index + 1 : anchor_index + 1 + scan_limit]:
            kind = str(item.get("semantic_kind") or "text")
            text = str(item.get("text") or "").strip()
            styles = {str(value).lower() for value in item.get("style_annotations") or ()}
            if not text:
                continue
            if kind in _STRUCTURAL_KINDS:
                if paragraphs:
                    break
                continue
            if kind not in _PLAYER_PROSE_KINDS or "italic" not in styles:
                if paragraphs:
                    break
                continue
            if _ROLE_ONLY.search(text):
                if paragraphs:
                    break
                continue
            paragraphs.append(text)
            if sum(len(value) for value in paragraphs) >= character_limit:
                break
        if paragraphs:
            return "\n\n".join(paragraphs)[:character_limit].strip()
    return None


__all__ = ["extract_public_opening"]
