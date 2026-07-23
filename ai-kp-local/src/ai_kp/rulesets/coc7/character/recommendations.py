"""Canonical editable CoC7 investigator skill recommendation implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_kp.rulesets.coc7.character.creation_rules import OCCUPATION_POINT_FORMULAS
from ai_kp.rulesets.coc7.character.skills import COC7_SKILL_CATALOG


PHYSICAL_SKILLS = {
    "coc7.climb",
    "coc7.dodge",
    "coc7.fighting_brawl",
    "coc7.jump",
    "coc7.ride",
    "coc7.swim",
    "coc7.throw",
}
MODERN_ONLY_SKILLS = {"coc7.computer_use", "coc7.electronics"}
GENERAL_INTEREST_SKILLS = (
    "coc7.first_aid",
    "coc7.stealth",
    "coc7.persuade",
    "coc7.language_other_1",
    "coc7.art_craft_2",
    "coc7.swim",
    "coc7.climb",
    "coc7.occult",
    "coc7.history",
    "coc7.listen",
    "coc7.navigate",
    "coc7.appraise",
)


@dataclass(frozen=True)
class RecommendationProfile:
    profile_id: str
    display_name: str
    keywords: tuple[str, ...]
    occupation_skills: tuple[str, ...]
    interest_skills: tuple[str, ...]
    specializations: dict[str, str]


PROFILES = (
    RecommendationProfile(
        "reporter",
        "记者/调查记者",
        ("记者", "新闻", "reporter", "journalist"),
        (
            "coc7.art_craft_1",
            "coc7.history",
            "coc7.library_use",
            "coc7.language_own",
            "coc7.psychology",
            "coc7.fast_talk",
            "coc7.spot_hidden",
            "coc7.credit_rating",
        ),
        ("coc7.listen", "coc7.persuade", "coc7.stealth", "coc7.first_aid"),
        {"coc7.art_craft_1": "摄影"},
    ),
    RecommendationProfile(
        "private_investigator",
        "私家侦探",
        ("私家侦探", "调查员", "detective", "investigator"),
        (
            "coc7.law",
            "coc7.library_use",
            "coc7.listen",
            "coc7.psychology",
            "coc7.spot_hidden",
            "coc7.disguise",
            "coc7.persuade",
            "coc7.credit_rating",
        ),
        ("coc7.stealth", "coc7.locksmith", "coc7.fighting_brawl", "coc7.first_aid"),
        {},
    ),
    RecommendationProfile(
        "police",
        "警察/探员",
        ("警察", "警官", "刑警", "police", "officer"),
        (
            "coc7.fighting_brawl",
            "coc7.firearms_handgun",
            "coc7.first_aid",
            "coc7.law",
            "coc7.listen",
            "coc7.psychology",
            "coc7.spot_hidden",
            "coc7.drive_auto",
            "coc7.credit_rating",
        ),
        ("coc7.intimidate", "coc7.stealth", "coc7.navigate", "coc7.mechanical_repair"),
        {},
    ),
    RecommendationProfile(
        "doctor",
        "医生/医学从业者",
        ("医生", "医师", "护士", "doctor", "physician", "surgeon"),
        (
            "coc7.first_aid",
            "coc7.medicine",
            "coc7.science_1",
            "coc7.science_2",
            "coc7.psychology",
            "coc7.library_use",
            "coc7.language_own",
            "coc7.persuade",
            "coc7.credit_rating",
        ),
        ("coc7.psychoanalysis", "coc7.language_other_1", "coc7.spot_hidden", "coc7.listen"),
        {"coc7.science_1": "生物学", "coc7.science_2": "药学"},
    ),
    RecommendationProfile(
        "academic",
        "教授/学者",
        ("教授", "学者", "研究员", "考古学家", "professor", "academic", "scholar"),
        (
            "coc7.library_use",
            "coc7.language_own",
            "coc7.language_other_1",
            "coc7.history",
            "coc7.psychology",
            "coc7.science_1",
            "coc7.lore",
            "coc7.persuade",
            "coc7.credit_rating",
        ),
        ("coc7.occult", "coc7.spot_hidden", "coc7.archaeology", "coc7.listen"),
        {"coc7.science_1": "专业领域", "coc7.lore": "学术专题"},
    ),
    RecommendationProfile(
        "engineer",
        "工程师/技师",
        ("工程师", "技师", "机械师", "engineer", "mechanic"),
        (
            "coc7.electrical_repair",
            "coc7.mechanical_repair",
            "coc7.operate_heavy_machinery",
            "coc7.science_1",
            "coc7.art_craft_1",
            "coc7.library_use",
            "coc7.drive_auto",
            "coc7.spot_hidden",
            "coc7.credit_rating",
        ),
        ("coc7.first_aid", "coc7.navigate", "coc7.appraise", "coc7.locksmith"),
        {"coc7.science_1": "物理学", "coc7.art_craft_1": "工程制图"},
    ),
    RecommendationProfile(
        "artist",
        "艺术家/工艺家",
        ("艺术家", "画家", "音乐家", "演员", "artist", "musician", "actor"),
        (
            "coc7.art_craft_1",
            "coc7.art_craft_2",
            "coc7.appraise",
            "coc7.history",
            "coc7.spot_hidden",
            "coc7.psychology",
            "coc7.charm",
            "coc7.persuade",
            "coc7.credit_rating",
        ),
        ("coc7.occult", "coc7.language_other_1", "coc7.fast_talk", "coc7.listen"),
        {"coc7.art_craft_1": "主要艺术", "coc7.art_craft_2": "辅助工艺"},
    ),
    RecommendationProfile(
        "lawyer",
        "律师/法律从业者",
        ("律师", "法官", "检察官", "lawyer", "attorney", "judge"),
        (
            "coc7.law",
            "coc7.library_use",
            "coc7.accounting",
            "coc7.psychology",
            "coc7.persuade",
            "coc7.fast_talk",
            "coc7.history",
            "coc7.language_own",
            "coc7.credit_rating",
        ),
        ("coc7.spot_hidden", "coc7.listen", "coc7.intimidate", "coc7.language_other_1"),
        {},
    ),
    RecommendationProfile(
        "military",
        "军人/退伍军人",
        ("军人", "士兵", "军官", "退伍", "soldier", "military", "veteran"),
        (
            "coc7.firearms_handgun",
            "coc7.firearms_rifle_shotgun",
            "coc7.fighting_brawl",
            "coc7.first_aid",
            "coc7.navigate",
            "coc7.survival",
            "coc7.spot_hidden",
            "coc7.listen",
            "coc7.credit_rating",
        ),
        ("coc7.stealth", "coc7.climb", "coc7.throw", "coc7.mechanical_repair"),
        {"coc7.survival": "野外"},
    ),
    RecommendationProfile(
        "criminal",
        "罪犯/地下从业者",
        ("罪犯", "盗贼", "黑帮", "小偷", "criminal", "thief", "gangster"),
        (
            "coc7.stealth",
            "coc7.sleight_of_hand",
            "coc7.locksmith",
            "coc7.spot_hidden",
            "coc7.disguise",
            "coc7.intimidate",
            "coc7.fighting_brawl",
            "coc7.drive_auto",
            "coc7.credit_rating",
        ),
        ("coc7.fast_talk", "coc7.listen", "coc7.firearms_handgun", "coc7.psychology"),
        {},
    ),
)

DEFAULT_PROFILE = RecommendationProfile(
    "general_investigator",
    "通用调查员",
    (),
    (
        "coc7.credit_rating",
        "coc7.spot_hidden",
        "coc7.listen",
        "coc7.library_use",
        "coc7.psychology",
        "coc7.first_aid",
        "coc7.persuade",
        "coc7.stealth",
    ),
    ("coc7.history", "coc7.occult", "coc7.appraise", "coc7.navigate"),
    {},
)


def _is_modern_era(era: str) -> bool:
    normalized = era.strip().lower()
    return any(
        token in normalized
        for token in ("现代", "modern", "199", "200", "201", "202")
    )


def _match_profile(occupation: str) -> RecommendationProfile:
    normalized = occupation.strip().lower()
    return next(
        (
            profile
            for profile in PROFILES
            if any(keyword.lower() in normalized for keyword in profile.keywords)
        ),
        DEFAULT_PROFILE,
    )


def _base_value(skill: dict[str, Any], characteristics: dict[str, int]) -> int:
    if skill["base_formula"] == "dex_half":
        return characteristics.get("dex", 0) // 2
    if skill["base_formula"] == "edu":
        return characteristics.get("edu", 0)
    return int(skill["base_value"])


def _occupation_budget(
    formula_key: str, characteristics: dict[str, int]
) -> int:
    formula = OCCUPATION_POINT_FORMULAS.get(formula_key)
    if not formula:
        return 0
    first_key, first_multiplier, second_key, second_multiplier = formula
    budget = characteristics.get(first_key, 0) * first_multiplier
    if second_key:
        budget += characteristics.get(second_key, 0) * second_multiplier
    return max(0, budget)


def _eligible_keys(keys: tuple[str, ...], modern_era: bool) -> list[str]:
    return list(
        dict.fromkeys(
            key for key in keys if modern_era or key not in MODERN_ONLY_SKILLS
        )
    )


def _allocate(
    budget: int,
    preferred_keys: list[str],
    current_values: dict[str, int],
    *,
    age: int,
    focus_count: int,
) -> dict[str, int]:
    allocations = {key: 0 for key in preferred_keys}
    remaining = max(0, budget)
    targets = (70, 65, 60, 60, 55, 55, 50, 50, 45, 45, 40, 40)
    focus_keys = preferred_keys[: max(1, focus_count)]
    focus_targets: dict[str, int] = {}
    for index, key in enumerate(focus_keys):
        target = targets[min(index, len(targets) - 1)]
        if age >= 50 and key in PHYSICAL_SKILLS:
            target = max(40, target - min(20, ((age - 40) // 10) * 5))
        focus_targets[key] = target
        baseline = min(target, 30 if age >= 50 and key in PHYSICAL_SKILLS else 35)
        available = max(0, baseline - current_values.get(key, 0))
        granted = min(remaining, available)
        allocations[key] += granted
        current_values[key] = current_values.get(key, 0) + granted
        remaining -= granted
        if remaining <= 0:
            break
    while remaining > 0:
        progressed = False
        for key in focus_keys:
            if current_values.get(key, 0) >= focus_targets[key]:
                continue
            allocations[key] += 1
            current_values[key] = current_values.get(key, 0) + 1
            remaining -= 1
            progressed = True
            if remaining <= 0:
                break
        if not progressed:
            break
    if remaining > 0:
        for key in preferred_keys:
            available = max(0, 75 - current_values.get(key, 0))
            granted = min(remaining, available)
            allocations[key] += granted
            current_values[key] = current_values.get(key, 0) + granted
            remaining -= granted
            if remaining <= 0:
                break
    return {key: value for key, value in allocations.items() if value > 0}


def recommend_coc7_skill_points(
    *,
    occupation: str,
    era: str,
    age: int,
    occupation_point_formula: str,
    characteristics: dict[str, int],
) -> dict[str, Any]:
    profile = _match_profile(occupation)
    modern_era = _is_modern_era(era)
    catalog = {entry["skill_key"]: dict(entry) for entry in COC7_SKILL_CATALOG}
    current_values = {
        key: _base_value(skill, characteristics) for key, skill in catalog.items()
    }
    occupation_keys = _eligible_keys(profile.occupation_skills, modern_era)
    occupation_focus_count = len(occupation_keys)
    fallback_keys = _eligible_keys(
        tuple(entry["skill_key"] for entry in COC7_SKILL_CATALOG), modern_era
    )
    occupation_keys.extend(
        key
        for key in fallback_keys
        if key not in occupation_keys and key != "coc7.cthulhu_mythos"
    )
    occupation_budget = _occupation_budget(occupation_point_formula, characteristics)
    occupation_allocations = _allocate(
        occupation_budget,
        occupation_keys,
        current_values,
        age=age,
        focus_count=occupation_focus_count,
    )

    interest_focus_count = len(_eligible_keys(profile.interest_skills, modern_era))
    interest_keys = _eligible_keys(
        profile.interest_skills + GENERAL_INTEREST_SKILLS, modern_era
    )
    interest_keys.extend(
        key
        for key in fallback_keys
        if key not in interest_keys
        and key not in occupation_allocations
        and key != "coc7.cthulhu_mythos"
    )
    interest_budget = max(0, characteristics.get("int", 0) * 2)
    interest_allocations = _allocate(
        interest_budget,
        interest_keys,
        current_values,
        age=age,
        focus_count=interest_focus_count,
    )

    all_keys = list(dict.fromkeys((*occupation_allocations, *interest_allocations)))
    allocations = [
        {
            "skill_key": key,
            "occupation_points": occupation_allocations.get(key, 0),
            "interest_points": interest_allocations.get(key, 0),
            "specialization": profile.specializations.get(key),
        }
        for key in all_keys
    ]
    matched = profile is not DEFAULT_PROFILE
    rationale = [
        f"匹配模板：{profile.display_name}"
        if matched
        else "未命中具体职业模板，使用通用调查员方案。",
        f"按 {occupation_point_formula} 分配 {occupation_budget} 点职业技能点。",
        f"按 INT×2 分配 {interest_budget} 点兴趣技能点。",
        "现代时代：可使用计算机使用和电子学。"
        if modern_era
        else "历史时代：推荐已排除现代专属技能。",
    ]
    if age >= 50:
        rationale.append("已根据年龄降低体能类技能的推荐优先级。")
    rationale.append("这是可编辑建议，不是规则强制；保存前仍可手动调整。")
    return {
        "profile_id": profile.profile_id,
        "profile_name": profile.display_name,
        "occupation_budget": occupation_budget,
        "occupation_spent": sum(occupation_allocations.values()),
        "interest_budget": interest_budget,
        "interest_spent": sum(interest_allocations.values()),
        "allocations": allocations,
        "rationale": rationale,
    }
