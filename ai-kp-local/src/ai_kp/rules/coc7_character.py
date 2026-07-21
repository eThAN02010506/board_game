from __future__ import annotations

from copy import deepcopy
from typing import Any


ATTRIBUTE_KEYS = ("str", "con", "siz", "dex", "app", "int", "pow", "edu")
OCCUPATION_POINT_FORMULAS = {
    "edu4": ("edu", 4, None, 0),
    "edu2_app2": ("edu", 2, "app", 2),
    "edu2_dex2": ("edu", 2, "dex", 2),
    "edu2_pow2": ("edu", 2, "pow", 2),
    "edu2_str2": ("edu", 2, "str", 2),
}


def _integer(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _damage_bonus_and_build(total: int) -> tuple[str, int]:
    if total <= 64:
        return "-2", -2
    if total <= 84:
        return "-1", -1
    if total <= 124:
        return "0", 0
    if total <= 164:
        return "+1D4", 1
    if total <= 204:
        return "+1D6", 2
    extra = max(0, (total - 205) // 80)
    return f"+{2 + extra}D6", 3 + extra


def _movement_rate(characteristics: dict[str, int], age: int) -> int:
    strength = characteristics["str"]
    dexterity = characteristics["dex"]
    size = characteristics["siz"]
    if strength < size and dexterity < size:
        movement = 7
    elif strength > size and dexterity > size:
        movement = 9
    else:
        movement = 8
    if age >= 80:
        movement -= 5
    elif age >= 70:
        movement -= 4
    elif age >= 60:
        movement -= 3
    elif age >= 50:
        movement -= 2
    elif age >= 40:
        movement -= 1
    return max(1, movement)


def normalize_character_sheet(sheet: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return a versioned, model-independent CoC7 character sheet.

    Imported totals and workbook formulas are deliberately ignored. Only normalized
    inputs reach this function; every derived number is rebuilt here.
    """

    canonical = deepcopy(sheet)
    warnings: list[str] = []
    identity = dict(canonical.get("identity") or {})
    provenance = dict(canonical.get("provenance") or {})
    characteristics_input = dict(canonical.get("characteristics") or {})
    characteristics = {
        key: _integer(characteristics_input.get(key)) for key in ATTRIBUTE_KEYS
    }
    luck = _integer(characteristics_input.get("luck"))
    age = _integer(identity.get("age"))

    for key, value in characteristics.items():
        if value <= 0:
            warnings.append(f"属性 {key.upper()} 尚未填写")
        elif key not in {"siz", "pow"} and value > 99:
            warnings.append(f"属性 {key.upper()} 超出常规人类范围")
    if luck <= 0:
        warnings.append("幸运尚未填写")
    if age and not 15 <= age <= 89:
        warnings.append("年龄不在常规调查员范围 15–89 内，需由 KP 确认")

    normalized_skills: list[dict[str, Any]] = []
    mythos = 0
    occupation_spent = 0
    interest_spent = 0
    for index, raw_skill in enumerate(canonical.get("skills") or []):
        if not isinstance(raw_skill, dict):
            warnings.append(f"第 {index + 1} 项技能格式无效")
            continue
        skill = dict(raw_skill)
        display_name = str(skill.get("display_name") or "").strip()
        specialization = str(skill.get("specialization") or "").strip()
        if not display_name:
            continue
        base = _integer(skill.get("base_value"))
        if display_name.startswith("闪避"):
            base = characteristics["dex"] // 2
        elif display_name.startswith("母语"):
            base = characteristics["edu"]
        occupation = max(0, _integer(skill.get("occupation_points")))
        interest = max(0, _integer(skill.get("interest_points")))
        occupation_spent += occupation
        interest_spent += interest
        development = max(0, _integer(skill.get("development_points")))
        total = base + occupation + interest + development
        if total > 99 and "克苏鲁神话" not in display_name:
            warnings.append(f"技能“{display_name}”超过 99，需由 KP 确认")
        elif total > 75 and "克苏鲁神话" not in display_name:
            warnings.append(f"技能“{display_name}”超过角色创建建议上限 75，需由 KP 确认")
        if "克苏鲁神话" in display_name:
            mythos = total
            if occupation or interest:
                warnings.append("克苏鲁神话不能在创建角色时分配职业点或兴趣点")
        normalized_skills.append(
            {
                "skill_key": str(skill.get("skill_key") or f"custom.{index + 1}"),
                "display_name": display_name,
                "specialization": specialization or None,
                "base_value": base,
                "occupation_points": occupation,
                "interest_points": interest,
                "development_points": development,
                "current_value": total,
                "half_value": total // 2,
                "fifth_value": total // 5,
                "growth_mark": bool(skill.get("growth_mark", False)),
            }
        )

    if provenance.get("source_type") == "manual":
        formula = OCCUPATION_POINT_FORMULAS.get(
            str(provenance.get("occupation_point_formula") or "")
        )
        if formula:
            first_key, first_multiplier, second_key, second_multiplier = formula
            occupation_budget = characteristics[first_key] * first_multiplier
            if second_key:
                occupation_budget += characteristics[second_key] * second_multiplier
            if occupation_spent > occupation_budget:
                warnings.append(
                    f"职业技能点超出预算 {occupation_spent - occupation_budget} 点"
                )
            elif occupation_spent < occupation_budget:
                warnings.append(
                    f"职业技能点尚有 {occupation_budget - occupation_spent} 点未分配"
                )
        interest_budget = characteristics["int"] * 2
        if interest_spent > interest_budget:
            warnings.append(f"兴趣技能点超出预算 {interest_spent - interest_budget} 点")
        elif interest_spent < interest_budget:
            warnings.append(f"兴趣技能点尚有 {interest_budget - interest_spent} 点未分配")

    db, build = _damage_bonus_and_build(characteristics["str"] + characteristics["siz"])
    derived = {
        "attribute_difficulties": {
            key: {"regular": value, "hard": value // 2, "extreme": value // 5}
            for key, value in characteristics.items()
        },
        "max_hp": (characteristics["con"] + characteristics["siz"]) // 10,
        "max_mp": characteristics["pow"] // 5,
        "initial_san": characteristics["pow"],
        "max_san": max(0, 99 - mythos),
        "mov": _movement_rate(characteristics, age),
        "damage_bonus": db,
        "build": build,
        "dodge": characteristics["dex"] // 2,
    }

    canonical["schema_version"] = "coc7-investigator-v1"
    canonical["ruleset_id"] = "coc7-keeper-cn-2002c"
    canonical["identity"] = identity
    canonical["characteristics"] = {**characteristics, "luck": luck}
    canonical["derived"] = derived
    canonical["skills"] = normalized_skills
    canonical.setdefault("combat", {"weapons": []})
    canonical.setdefault("assets", {"items": []})
    canonical.setdefault("background", {})
    canonical["provenance"] = provenance
    return canonical, list(dict.fromkeys(warnings))
