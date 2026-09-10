"""CoC7 mappings for scenario-authored abstract check terms."""

from ai_kp.platform.resolution.check_catalog import (
    ScenarioCheckCatalog,
    ScenarioCheckCatalogEntry,
)
from ai_kp.rulesets.coc7.character.skills import list_coc7_skill_catalog

_CONCEPT_ALIASES: dict[str, tuple[str, ...]] = {
    "coc7.spot_hidden": ("观察", "搜索", "寻找", "感知", "perception"),
    "coc7.listen": ("听觉", "倾听", "感知", "perception"),
    "coc7.library_use": ("调查资料", "研究", "查阅", "research"),
    "coc7.psychology": ("洞察", "识破", "判断动机", "insight"),
    "coc7.credit_rating": ("财富", "现金", "购买力", "贿赂", "wealth"),
    "coc7.accounting": ("账本", "账目", "财务记录", "bookkeeping"),
    "coc7.disguise": ("化装", "伪装身份", "假扮", "disguise"),
    "coc7.charm": ("社交", "取悦", "social"),
    "coc7.fast_talk": ("欺骗", "说谎", "诈骗", "social", "deception"),
    "coc7.persuade": ("社交", "交涉", "劝说", "social", "persuasion"),
    "coc7.intimidate": ("社交", "威胁", "施压", "social", "intimidation"),
    "coc7.mechanical_repair": ("修理", "维修", "技术", "repair"),
    "coc7.electrical_repair": ("修理", "维修", "技术", "repair"),
    "coc7.locksmith": ("开锁", "锁具", "lock"),
    "coc7.navigate": ("找路", "路线", "方向", "navigation"),
    "coc7.stealth": ("隐蔽", "偷偷", "不被发现", "stealth"),
    "coc7.track": ("追踪痕迹", "跟踪", "tracking"),
    "coc7.first_aid": ("治疗", "救治", "medical"),
    "coc7.medicine": ("治疗", "诊断", "medical"),
    "coc7.climb": ("体能", "攀登", "athletics"),
    "coc7.jump": ("体能", "跨越", "athletics"),
    "coc7.swim": ("体能", "水中", "athletics"),
}

_CHARACTERISTICS = (
    ScenarioCheckCatalogEntry(
        check_key="san",
        display_name="理智",
        direct_aliases=("SAN", "sanity", "理智值"),
        concept_aliases=("理智检定", "理智损失"),
    ),
    ScenarioCheckCatalogEntry(
        check_key="int",
        display_name="智力",
        direct_aliases=("INT", "灵感", "idea", "intelligence"),
        concept_aliases=("推理", "联想", "想法", "idea"),
    ),
    ScenarioCheckCatalogEntry(
        check_key="luck",
        display_name="幸运",
        direct_aliases=("luck",),
        concept_aliases=("运气", "偶然"),
    ),
    ScenarioCheckCatalogEntry(
        check_key="dex",
        display_name="敏捷",
        direct_aliases=("DEX", "dexterity"),
        concept_aliases=("反应", "灵活", "agility"),
    ),
    ScenarioCheckCatalogEntry(
        check_key="str",
        display_name="力量",
        direct_aliases=("STR", "strength"),
        concept_aliases=("蛮力", "力气"),
    ),
    ScenarioCheckCatalogEntry(
        check_key="con",
        display_name="体质",
        direct_aliases=("CON", "constitution"),
        concept_aliases=("耐力", "忍耐", "endurance"),
    ),
    ScenarioCheckCatalogEntry(
        check_key="pow",
        display_name="意志",
        direct_aliases=("POW", "power"),
        concept_aliases=("意志力", "精神抵抗", "willpower"),
    ),
    ScenarioCheckCatalogEntry(
        check_key="edu",
        display_name="教育",
        direct_aliases=("EDU", "education"),
        concept_aliases=("常识", "学识", "knowledge"),
    ),
)


def coc7_scenario_check_catalog() -> ScenarioCheckCatalog:
    skills = tuple(
        ScenarioCheckCatalogEntry(
            check_key=str(item["skill_key"]),
            display_name=str(item["display_name"]),
            direct_aliases=tuple(
                value
                for value in (
                    str(item["skill_key"]).removeprefix("coc7."),
                    item.get("default_specialization"),
                )
                if value
            ),
            concept_aliases=_CONCEPT_ALIASES.get(str(item["skill_key"]), ()),
        )
        for item in list_coc7_skill_catalog()
    )
    return ScenarioCheckCatalog(
        ruleset_id="coc7-keeper-cn-2002c",
        entries=(*_CHARACTERISTICS, *skills),
    )


__all__ = ["coc7_scenario_check_catalog"]
