"""Deterministic, source-preserving hints for heterogeneous scenario documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

SemanticKind = Literal[
    "heading",
    "scenario_metadata",
    "keeper_overview",
    "scene",
    "read_aloud",
    "clue",
    "check",
    "san_check",
    "npc",
    "stat_block",
    "handout",
    "ending",
    "optional_branch",
    "reference",
    "table",
    "text",
]
AssetRole = Literal[
    "map",
    "handout",
    "portrait",
    "scene_art",
    "pregen",
    "decorative",
    "unknown",
]

_MANUAL_HEADING = re.compile(r"^[<＜【\[][^>＞】\]]{1,80}[>＞】\]]$")
_NUMBERED_HEADING = re.compile(
    r"^(?:chapter|第[一二三四五六七八九十百0-9]+[章节幕部分]|"
    r"[一二三四五六七八九十0-9]+[、.．])\s*\S+",
    re.IGNORECASE,
)
_DATE_LIKE = re.compile(r"^\d{2,4}(?:[./-]\d{1,2}){1,2}\.?$")
_SCENE_LABEL = re.compile(
    r"^(?:场景|地点|scene|location)\s*[:：]?\s*\S+",
    re.IGNORECASE,
)
_CHECK = re.compile(
    r"(?:进行|通过|要求|需要|可做|必须)?.{0,24}?"
    r"(?:《[^》《\n]{1,40}》|[A-Za-z][A-Za-z0-9 _-]{0,39}|[\u3400-\u9fff]{1,16})?"
    r"(?:检定|判定|鉴定)(?:.{0,12}(?:成功|失败))?",
    re.IGNORECASE,
)
_SAN = re.compile(r"(?:san|理智).{0,12}(?:检定|损失|失去|\d\s*/\s*(?:1?d)?\d)", re.IGNORECASE)
_STATS = re.compile(
    r"(?:str|con|siz|dex|app|int|pow|edu|力量|体质|体型|敏捷|外貌|智力|意志|教育)"
    r".{0,80}(?:hp|生命值|db|伤害加值|build|体格)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class StructureHint:
    semantic_kind: SemanticKind
    confidence: float
    review_flags: tuple[str, ...] = ()


def looks_like_heading(text: str, *, styled_heading: bool = False) -> bool:
    normalized = " ".join(text.split()).strip()
    if not normalized or len(normalized) > 120:
        return False
    return bool(
        styled_heading
        or _MANUAL_HEADING.fullmatch(normalized)
        or (
            len(normalized) <= 40
            and not _DATE_LIKE.fullmatch(normalized)
            and _NUMBERED_HEADING.match(normalized)
        )
        or normalized.casefold()
        in {
            "概要",
            "导入",
            "背景",
            "真相",
            "结局",
            "尾声",
            "展示材料",
            "预设角色",
            "预设调查员",
            "keeper information",
            "scenario overview",
            "conclusion",
            "handouts",
        }
    )


def infer_semantic_kind(
    text: str,
    *,
    content_kind: str = "text",
    styled_heading: bool = False,
) -> StructureHint:
    normalized = " ".join(text.split()).strip()
    lowered = normalized.casefold()
    if content_kind == "table":
        return StructureHint("table", 1.0)
    if _SCENE_LABEL.match(normalized) and len(normalized) < 120:
        return StructureHint(
            "scene",
            0.9 if styled_heading else 0.68,
            ("verify_scene_boundary",),
        )
    if looks_like_heading(normalized, styled_heading=styled_heading):
        return StructureHint("heading", 0.98 if styled_heading else 0.9)
    if _SAN.search(normalized):
        return StructureHint("san_check", 0.94)
    if _STATS.search(normalized):
        return StructureHint("stat_block", 0.88)
    if _CHECK.search(normalized):
        return StructureHint("check", 0.86)
    if any(key in lowered for key in ("预设调查员", "预设角色", "pregenerated investigator")):
        return StructureHint("reference", 0.9)
    if any(key in lowered for key in ("玩家资料", "展示材料", "handout")):
        return StructureHint("handout", 0.9)
    if any(key in lowered for key in ("可选结局", "另一结局", "optional ending")):
        return StructureHint("optional_branch", 0.88)
    if any(key in lowered for key in ("结局", "尾声", "conclusion")):
        return StructureHint("ending", 0.82)
    if any(key in lowered for key in ("概要", "模组背景", "故事背景", "keeper information")):
        return StructureHint("keeper_overview", 0.82)
    if any(key in lowered for key in ("游戏时间", "建议人数", "推荐技能", "难度")):
        return StructureHint("scenario_metadata", 0.82)
    if any(key in lowered for key in ("线索", "可以发现", "调查员发现", "察觉到")):
        return StructureHint("clue", 0.72, ("verify_clue_role",))
    return StructureHint("text", 0.5)


def inherit_heading_semantics(
    hint: StructureHint,
    heading: str,
) -> StructureHint:
    """Apply a strong enclosing section only to otherwise unclassified prose."""

    if hint.semantic_kind != "text":
        return hint
    normalized = " ".join(heading.split()).strip().casefold()
    if normalized in {"结局", "尾声", "conclusion", "epilogue"}:
        return StructureHint("ending", 0.9)
    if normalized in {"展示材料", "玩家资料", "handouts"}:
        return StructureHint("handout", 0.88)
    if normalized in {"预设角色", "预设调查员", "pregenerated investigators"}:
        return StructureHint("reference", 0.88)
    return hint


def infer_asset_role(
    *,
    filename: str,
    nearby_heading: str | None,
    width: int | None,
    height: int | None,
) -> tuple[AssetRole, float, tuple[str, ...]]:
    label = f"{filename} {nearby_heading or ''}".casefold()
    if width and height and width <= 32 and height <= 32:
        return "decorative", 0.99, ()
    if any(key in label for key in ("map", "地图", "平面图", "路线图")):
        return "map", 0.92, ()
    if any(key in label for key in ("handout", "展示材料", "玩家资料", "线索图")):
        return "handout", 0.88, ()
    if any(key in label for key in ("预设调查员", "预设角色", "pregen")):
        return "pregen", 0.88, ()
    if any(key in label for key in ("portrait", "头像", "肖像", "人物")):
        return "portrait", 0.78, ()
    if width and height and width * height >= 300_000:
        return "scene_art", 0.62, ("verify_asset_role",)
    return "unknown", 0.3, ("needs_asset_review",)
