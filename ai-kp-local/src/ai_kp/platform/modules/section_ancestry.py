"""Narrow, auditable heading ancestry for structure-preserving ingestion."""

from __future__ import annotations

import re
import unicodedata

_TOP_LEVEL = re.compile(
    r"^(?:"
    r"场景\s*(?:\d+|[0-9a-z一二三四五六七八九十百]+)|"
    r"scene\s*(?:\d+|[0-9a-z]+)|"
    r"结局|尾声|序幕|导入|开场|背景|故事背景|模组背景|守秘人信息|"
    r"ending|epilogue|setup|scenario\s+setup|introduction"
    r")(?:\s*[:\-].*)?$",
    re.IGNORECASE,
)
_FLOOR = re.compile(
    r"^(?:[地一二三四五六七八九十]+\s*楼|"
    r"地下室|阁楼|"
    r"(?:ground|first|second|third|fourth|fifth)\s+floor|basement|attic)"
    r"(?:\s*[:\-].*)?$",
    re.IGNORECASE,
)
_ROOM = re.compile(
    r"^(?:(?:\d+|[一二三四五六七八九十百]+)\s*号\s*房(?:间)?|"
    r"房间\s*(?:\d+|[一二三四五六七八九十百]+))"
    r"(?:\s*[:\-].*)?$",
    re.IGNORECASE,
)
_SCENE_TITLE = re.compile(
    r"^(?:场景\s*(?:\d+|[0-9a-z一二三四五六七八九十百]+)|scene\s*(?:\d+|[0-9a-z]+))"
    r"\s*[:\-]\s*(?P<title>.+)$",
    re.IGNORECASE,
)


def inferred_heading_level(title: str) -> int | None:
    """Return only levels justified by explicit, format-stable section labels."""

    normalized = unicodedata.normalize("NFKC", " ".join(title.split())).strip()
    normalized = normalized.replace("：", ":")
    if _TOP_LEVEL.fullmatch(normalized):
        return 1
    if _FLOOR.fullmatch(normalized):
        return 2
    if _ROOM.fullmatch(normalized):
        return 3
    return None


def resolve_heading_level(
    *,
    title: str,
    is_heading: bool,
    explicit_level: int | None,
    structural_parent_level: int | None,
    prefer_structural_parent_for_generic_explicit: bool = False,
) -> tuple[int | None, int | None]:
    """Resolve one heading and retain only an explicit structural parent.

    A generic short heading may be a child of a known structural section, but
    never becomes the parent of the next generic heading. This keeps adjacent
    attack/method headings as siblings instead of inventing arbitrary depth.
    """

    if not is_heading:
        return None, structural_parent_level
    structural_level = inferred_heading_level(title)
    if structural_level is not None:
        return structural_level, structural_level
    if explicit_level is not None and not (
        prefer_structural_parent_for_generic_explicit
        and structural_parent_level is not None
        and explicit_level <= structural_parent_level
    ):
        return explicit_level, explicit_level
    if structural_parent_level is not None and structural_parent_level < 9:
        return structural_parent_level + 1, structural_parent_level
    return None, structural_parent_level


def inferred_scene_key(section_path: tuple[str, ...]) -> str | None:
    """Return only a title explicitly attached to a numbered scene heading."""

    for component in reversed(section_path):
        normalized = unicodedata.normalize("NFKC", " ".join(component.split())).strip()
        normalized = normalized.replace("：", ":")
        match = _SCENE_TITLE.fullmatch(normalized)
        if match is not None:
            title = match.group("title").strip()
            return title[:160] or None
    return None


__all__ = ["inferred_heading_level", "inferred_scene_key", "resolve_heading_level"]
