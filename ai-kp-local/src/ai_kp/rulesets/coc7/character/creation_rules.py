"""Deterministic CoC7 investigator-creation validation."""

from typing import Any

from ai_kp.rulesets.coc7.character.normalization import ATTRIBUTE_KEYS
from ai_kp.rulesets.sdk.characters import CharacterValidationIssue

OCCUPATION_POINT_FORMULAS = {
    "edu4": ("edu", 4, None, 0),
    "edu2_app2": ("edu", 2, "app", 2),
    "edu2_dex2": ("edu", 2, "dex", 2),
    "edu2_pow2": ("edu", 2, "pow", 2),
    "edu2_str2": ("edu", 2, "str", 2),
}


def validate_structure(
    raw_sheet: dict[str, Any],
    canonical: dict[str, Any],
) -> list[CharacterValidationIssue]:
    return [
        *_validate_input_containers(raw_sheet),
        *_validate_required_values(canonical),
    ]


def _validate_input_containers(
    raw_sheet: dict[str, Any],
) -> list[CharacterValidationIssue]:
    issues: list[CharacterValidationIssue] = []
    for section in ("identity", "characteristics", "provenance"):
        value = raw_sheet.get(section)
        if value is not None and not isinstance(value, dict):
            issues.append(
                CharacterValidationIssue(
                    layer="structure",
                    code="invalid_section",
                    path=section,
                    message=f"角色卡字段 {section} 格式无效，已按空内容处理",
                )
            )
    raw_skills = raw_sheet.get("skills")
    if raw_skills is not None and not isinstance(raw_skills, list):
        issues.append(
            CharacterValidationIssue(
                layer="structure",
                code="invalid_skills",
                path="skills",
                message="技能列表格式无效，已按空列表处理",
            )
        )
    elif isinstance(raw_skills, list):
        for index, skill in enumerate(raw_skills):
            if not isinstance(skill, dict):
                issues.append(
                    CharacterValidationIssue(
                        layer="structure",
                        code="invalid_skill",
                        path=f"skills.{index}",
                        message=f"第 {index + 1} 项技能格式无效",
                    )
                )

    return issues


def _validate_required_values(
    canonical: dict[str, Any],
) -> list[CharacterValidationIssue]:
    issues: list[CharacterValidationIssue] = []
    if not str(canonical["identity"].get("name") or "").strip():
        issues.append(
            CharacterValidationIssue(
                layer="structure",
                code="missing_name",
                path="identity.name",
                message="调查员姓名尚未填写",
            )
        )
    characteristics = canonical["characteristics"]
    for key in ATTRIBUTE_KEYS:
        if characteristics[key] <= 0:
            issues.append(
                CharacterValidationIssue(
                    layer="structure",
                    code="missing_characteristic",
                    path=f"characteristics.{key}",
                    message=f"属性 {key.upper()} 尚未填写",
                )
            )
    if characteristics["luck"] <= 0:
        issues.append(
            CharacterValidationIssue(
                layer="structure",
                code="missing_luck",
                path="characteristics.luck",
                message="幸运尚未填写",
            )
        )
    return issues


def validate_creation_rules(
    canonical: dict[str, Any],
) -> list[CharacterValidationIssue]:
    issues: list[CharacterValidationIssue] = []
    skills = canonical["skills"]
    for index, skill in enumerate(skills):
        if "克苏鲁神话" in skill["display_name"] and (
            skill["occupation_points"] or skill["interest_points"]
        ):
            issues.append(
                CharacterValidationIssue(
                    layer="ruleset",
                    code="mythos_creation_points",
                    path=f"skills.{index}",
                    message="克苏鲁神话不能在创建角色时分配职业点或兴趣点",
                )
            )

    provenance = canonical["provenance"]
    if provenance.get("source_type") != "manual":
        return issues
    characteristics = canonical["characteristics"]
    occupation_spent = sum(skill["occupation_points"] for skill in skills)
    interest_spent = sum(skill["interest_points"] for skill in skills)
    formula = OCCUPATION_POINT_FORMULAS.get(
        str(provenance.get("occupation_point_formula") or "")
    )
    if formula:
        first_key, first_multiplier, second_key, second_multiplier = formula
        occupation_budget = characteristics[first_key] * first_multiplier
        if second_key:
            occupation_budget += characteristics[second_key] * second_multiplier
        difference = occupation_spent - occupation_budget
        if difference:
            issues.append(
                CharacterValidationIssue(
                    layer="ruleset",
                    code="occupation_budget",
                    path="skills",
                    message=(
                        f"职业技能点超出预算 {difference} 点"
                        if difference > 0
                        else f"职业技能点尚有 {-difference} 点未分配"
                    ),
                )
            )
    interest_budget = characteristics["int"] * 2
    difference = interest_spent - interest_budget
    if difference:
        issues.append(
            CharacterValidationIssue(
                layer="ruleset",
                code="interest_budget",
                path="skills",
                message=(
                    f"兴趣技能点超出预算 {difference} 点"
                    if difference > 0
                    else f"兴趣技能点尚有 {-difference} 点未分配"
                ),
            )
        )
    return issues


def validate_review_policy(
    canonical: dict[str, Any],
) -> list[CharacterValidationIssue]:
    issues: list[CharacterValidationIssue] = []
    characteristics = canonical["characteristics"]
    for key in ATTRIBUTE_KEYS:
        value = characteristics[key]
        if value > 99 and key not in {"siz", "pow"}:
            issues.append(
                CharacterValidationIssue(
                    layer="review_policy",
                    code="unusual_characteristic",
                    path=f"characteristics.{key}",
                    message=f"属性 {key.upper()} 超出常规人类范围",
                    requires_kp_review=True,
                )
            )
    age = int(canonical["identity"].get("age") or 0)
    if age and not 15 <= age <= 89:
        issues.append(
            CharacterValidationIssue(
                layer="review_policy",
                code="unusual_age",
                path="identity.age",
                message="年龄不在常规调查员范围 15–89 内，需由 KP 确认",
                requires_kp_review=True,
            )
        )
    for index, skill in enumerate(canonical["skills"]):
        total = skill["current_value"]
        if "克苏鲁神话" in skill["display_name"]:
            continue
        if total > 99:
            message = f"技能“{skill['display_name']}”超过 99，需由 KP 确认"
            code = "skill_above_99"
        elif total > 75:
            message = (
                f"技能“{skill['display_name']}”超过角色创建建议上限 75，需由 KP 确认"
            )
            code = "skill_above_creation_limit"
        else:
            continue
        issues.append(
            CharacterValidationIssue(
                layer="review_policy",
                code=code,
                path=f"skills.{index}.current_value",
                message=message,
                requires_kp_review=True,
            )
        )
    return issues
