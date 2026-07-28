"""Pure CoC7 character canonicalization and derived-value calculation."""

from copy import deepcopy
from typing import Any

ATTRIBUTE_KEYS = ("str", "con", "siz", "dex", "app", "int", "pow", "edu")


def integer(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def damage_bonus_and_build(total: int) -> tuple[str, int]:
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


def movement_rate(characteristics: dict[str, int], age: int) -> int:
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


def canonicalize_character_sheet(sheet: dict[str, Any]) -> dict[str, Any]:
    """Normalize inputs and recompute every derived value."""

    canonical = deepcopy(sheet)
    identity = mapping(canonical.get("identity"))
    provenance = mapping(canonical.get("provenance"))
    characteristics_input = mapping(canonical.get("characteristics"))
    characteristics = {
        key: integer(characteristics_input.get(key)) for key in ATTRIBUTE_KEYS
    }
    luck = integer(characteristics_input.get("luck"))
    age = integer(identity.get("age"))

    normalized_skills: list[dict[str, Any]] = []
    mythos = 0
    skills = canonical.get("skills")
    for index, raw_skill in enumerate(skills if isinstance(skills, list) else []):
        if not isinstance(raw_skill, dict):
            continue
        skill = dict(raw_skill)
        display_name = str(skill.get("display_name") or "").strip()
        specialization = str(skill.get("specialization") or "").strip()
        if not display_name:
            continue
        base = integer(skill.get("base_value"))
        if display_name.startswith("闪避"):
            base = characteristics["dex"] // 2
        elif display_name.startswith("母语"):
            base = characteristics["edu"]
        occupation = max(0, integer(skill.get("occupation_points")))
        interest = max(0, integer(skill.get("interest_points")))
        development = max(0, integer(skill.get("development_points")))
        total = base + occupation + interest + development
        if "克苏鲁神话" in display_name:
            mythos = total
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

    db, build = damage_bonus_and_build(characteristics["str"] + characteristics["siz"])
    canonical["schema_version"] = "coc7-investigator-v1"
    canonical["ruleset_id"] = "coc7-keeper-cn-2002c"
    canonical["identity"] = identity
    canonical["characteristics"] = {**characteristics, "luck": luck}
    canonical["derived"] = {
        "attribute_difficulties": {
            key: {"regular": value, "hard": value // 2, "extreme": value // 5}
            for key, value in characteristics.items()
        },
        "max_hp": (characteristics["con"] + characteristics["siz"]) // 10,
        "max_mp": characteristics["pow"] // 5,
        "initial_san": characteristics["pow"],
        "max_san": max(0, 99 - mythos),
        "mov": movement_rate(characteristics, age),
        "damage_bonus": db,
        "build": build,
        "dodge": characteristics["dex"] // 2,
    }
    canonical["skills"] = normalized_skills
    canonical.setdefault("combat", {"weapons": []})
    canonical.setdefault("assets", {"items": []})
    canonical.setdefault("background", {})
    canonical["provenance"] = provenance
    return canonical
