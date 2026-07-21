from __future__ import annotations

import hashlib
import re
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from ai_kp.rules.coc7_character import normalize_character_sheet


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MAX_XLSX_BYTES = 8 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024


def _text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t")) for item in root]


def _main_sheet_path(archive: ZipFile) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    target_id = None
    for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet"):
        if sheet.attrib.get("name") == "人物卡":
            target_id = sheet.attrib.get(f"{{{REL_NS}}}id")
            break
    if not target_id:
        raise ValueError("未识别到“人物卡”工作表")
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relationship in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
        if relationship.attrib.get("Id") == target_id:
            target = unquote(relationship.attrib["Target"])
            normalized = PurePosixPath("xl") / target
            return str(normalized)
    raise ValueError("人物卡工作表关系损坏")


def _read_cells(archive: ZipFile, sheet_path: str) -> tuple[dict[str, Any], set[str]]:
    shared = _shared_strings(archive)
    root = ElementTree.fromstring(archive.read(sheet_path))
    cells: dict[str, Any] = {}
    formulas: set[str] = set()
    for cell in root.findall(f".//{{{MAIN_NS}}}c"):
        reference = cell.attrib.get("r")
        if not reference:
            continue
        if cell.find(f"{{{MAIN_NS}}}f") is not None:
            formulas.add(reference)
            continue
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            cells[reference] = "".join(
                node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t")
            )
            continue
        value_node = cell.find(f"{{{MAIN_NS}}}v")
        if value_node is None or value_node.text is None:
            continue
        raw = value_node.text
        if cell_type == "s":
            index = int(raw)
            cells[reference] = shared[index] if 0 <= index < len(shared) else ""
        elif cell_type in {"str", "e"}:
            cells[reference] = raw
        elif cell_type == "b":
            cells[reference] = raw == "1"
        else:
            try:
                number = float(raw)
                cells[reference] = int(number) if number.is_integer() else number
            except ValueError:
                cells[reference] = raw
    return cells, formulas


def _skill_key(name: str, specialization: str, row: int, side: str) -> str:
    numbered_name = name.translate(str.maketrans({"①": "1", "②": "2", "③": "3"}))
    normalized = re.sub(r"[：:\sΩ]", "", numbered_name).lower()
    return normalized or f"row-{row}-{side}"


def _skill_base(name: str, specialization: str, raw: Any, attrs: dict[str, int]) -> int:
    if raw is not None:
        return max(0, _integer(raw))
    if name.startswith("闪避"):
        return attrs["dex"] // 2
    if name.startswith("母语"):
        return attrs["edu"]
    if name.startswith("格斗"):
        return 25 if specialization == "斗殴" else 0
    if name.startswith("射击"):
        if "手枪" in specialization:
            return 20
        if "步枪" in specialization or "霰弹枪" in specialization:
            return 25
        return 0
    for prefix, base in (
        ("科学", 1),
        ("技艺", 5),
        ("外语", 1),
        ("驾驶", 1),
        ("生存", 10),
        ("学问", 1),
        ("自定义技能", 1),
    ):
        if name.startswith(prefix):
            return base
    return 0


def _extract_skills(cells: dict[str, Any], attrs: dict[str, int]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in range(16, 50):
        for side, columns in (
            ("left", ("B", "D", "E", "G", "H", "I", "J")),
            ("right", ("N", "P", "Q", "R", "S", "T", "U")),
        ):
            mark_col, name_col, specialization_col, base_col, growth_col, occupation_col, interest_col = columns
            name = _text(cells.get(f"{name_col}{row}"))
            if not name:
                continue
            specialization = _text(cells.get(f"{specialization_col}{row}"))
            result.append(
                {
                    "skill_key": _skill_key(name, specialization, row, side),
                    "display_name": name,
                    "specialization": specialization or None,
                    "base_value": _skill_base(name, specialization, cells.get(f"{base_col}{row}"), attrs),
                    "development_points": _integer(cells.get(f"{growth_col}{row}")),
                    "occupation_points": _integer(cells.get(f"{occupation_col}{row}")),
                    "interest_points": _integer(cells.get(f"{interest_col}{row}")),
                    "growth_mark": _text(cells.get(f"{mark_col}{row}")) == "☑",
                }
            )
    return result


def import_coc_character_xlsx(data: bytes, filename: str = "character.xlsx") -> dict[str, Any]:
    if not data or len(data) > MAX_XLSX_BYTES:
        raise ValueError("Excel 文件为空或超过 8 MiB 限制")
    try:
        with ZipFile(BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_XLSX_UNCOMPRESSED_BYTES:
                raise ValueError("Excel 解压后超过 32 MiB 安全限制")
            names = set(archive.namelist())
            if "xl/workbook.xml" not in names:
                raise ValueError("文件不是有效的 XLSX 工作簿")
            if any(name.endswith("vbaProject.bin") for name in names):
                raise ValueError("不接受包含 VBA 宏的角色卡")
            if any(name.startswith("xl/externalLinks/") for name in names):
                raise ValueError("不接受包含外部链接的角色卡")
            sheet_path = _main_sheet_path(archive)
            cells, formulas = _read_cells(archive, sheet_path)
    except BadZipFile as exc:
        raise ValueError("文件不是有效的 XLSX 工作簿") from exc

    if _text(cells.get("B2")) != "调查员信息" or "STR" not in _text(cells.get("J3")):
        raise ValueError("Excel 布局与支持的 COC 人物卡模板不匹配")

    attrs = {
        "str": _integer(cells.get("K3")),
        "dex": _integer(cells.get("N3")),
        "pow": _integer(cells.get("Q3")),
        "con": _integer(cells.get("K5")),
        "app": _integer(cells.get("N5")),
        "edu": _integer(cells.get("Q5")),
        "siz": _integer(cells.get("K7")),
        "int": _integer(cells.get("N7")),
        "luck": _integer(cells.get("M10")),
    }
    background_cells = {
        "appearance": "M62",
        "beliefs": "M64",
        "significant_people": "M66",
        "significant_places": "M68",
        "treasured_possessions": "M70",
        "traits": "M72",
        "secret": "M74",
        "injuries_scars": "M76",
        "phobias_manias": "M78",
    }
    items = []
    for row in range(79, 86):
        item_name = _text(cells.get(f"D{row}"))
        if item_name:
            items.append(
                {
                    "name": item_name,
                    "visibility": _text(cells.get(f"B{row}")) or "未指定",
                    "location": _text(cells.get(f"C{row}")) or None,
                }
            )
    sheet = {
        "identity": {
            "name": _text(cells.get("C3")),
            "player_name": _text(cells.get("C4")),
            "era": _text(cells.get("G4")),
            "occupation": _text(cells.get("G5")) or _text(cells.get("C5")),
            "age": _integer(cells.get("C6")),
            "gender": _text(cells.get("F6")),
            "residence": _text(cells.get("C7")),
            "birthplace": _text(cells.get("F7")),
        },
        "characteristics": attrs,
        "skills": _extract_skills(cells, attrs),
        "combat": {"weapons": []},
        "assets": {"items": items, "currency": _text(cells.get("K62")) or None},
        "background": {key: _text(cells.get(cell)) for key, cell in background_cells.items()},
        "provenance": {
            "source_type": "xlsx",
            "source_filename": filename,
            "source_hash": hashlib.sha256(data).hexdigest(),
            "template_id": "coc-character-sheet-cn-people-card-v1",
            "parser_version": "1",
            "ignored_formula_cells": len(formulas),
        },
    }
    canonical, warnings = normalize_character_sheet(sheet)
    if not canonical["identity"].get("name"):
        warnings.insert(0, "调查员姓名尚未填写")
    return {
        "canonical_sheet": canonical,
        "warnings": list(dict.fromkeys(warnings)),
        "source_hash": hashlib.sha256(data).hexdigest(),
        "source_filename": filename,
        "template_id": "coc-character-sheet-cn-people-card-v1",
        "parser_version": "1",
        "ignored_formula_cells": len(formulas),
    }
