"""Canonical CoC7 investigator skill catalogue."""

from __future__ import annotations

from typing import Literal, TypedDict

BaseFormula = Literal["fixed", "dex_half", "edu"]


class SkillCatalogEntry(TypedDict):
    skill_key: str
    display_name: str
    default_specialization: str | None
    base_value: int
    base_formula: BaseFormula
    specialization_editable: bool
    creation_points_allowed: bool
    description: str
    order: int


def _skill(
    skill_key: str,
    display_name: str,
    base_value: int,
    *,
    specialization: str | None = None,
    base_formula: BaseFormula = "fixed",
    specialization_editable: bool = False,
    creation_points_allowed: bool = True,
) -> tuple[str, str, int, str | None, BaseFormula, bool, bool]:
    return (
        skill_key,
        display_name,
        base_value,
        specialization,
        base_formula,
        specialization_editable,
        creation_points_allowed,
    )


_SKILL_ROWS = (
    _skill("credit_rating", "信用评级", 0),
    _skill("occult", "神秘学", 5),
    _skill("cthulhu_mythos", "克苏鲁神话", 0, creation_points_allowed=False),
    _skill("archaeology", "考古学", 1),
    _skill("spot_hidden", "侦查", 25),
    _skill("history", "历史", 5),
    _skill("listen", "聆听", 20),
    _skill("natural_world", "博物学", 10),
    _skill("library_use", "图书馆使用", 20),
    _skill("anthropology", "人类学", 1),
    _skill("computer_use", "计算机使用 Ω", 5),
    _skill("science_1", "科学①", 1, specialization_editable=True),
    _skill("stealth", "潜行", 20),
    _skill("science_2", "科学②", 1, specialization_editable=True),
    _skill("track", "追踪", 10),
    _skill("science_3", "科学③", 1, specialization_editable=True),
    _skill("disguise", "乔装", 5),
    _skill("electronics", "电子学 Ω", 1),
    _skill("locksmith", "锁匠", 1),
    _skill("law", "法律", 5),
    _skill("sleight_of_hand", "妙手", 10),
    _skill("accounting", "会计", 5),
    _skill("appraise", "估价", 5),
    _skill(
        "art_craft_1",
        "技艺①",
        5,
        specialization="钢琴",
        specialization_editable=True,
    ),
    _skill("charm", "魅惑", 15),
    _skill("art_craft_2", "技艺②", 5, specialization_editable=True),
    _skill("fast_talk", "话术", 5),
    _skill("art_craft_3", "技艺③", 5, specialization_editable=True),
    _skill("intimidate", "恐吓", 15),
    _skill("electrical_repair", "电气维修", 10),
    _skill("persuade", "说服", 10),
    _skill("mechanical_repair", "机械维修", 10),
    _skill("psychology", "心理学", 10),
    _skill("operate_heavy_machinery", "操作重型机械", 1),
    _skill("dodge", "闪避", 0, base_formula="dex_half"),
    _skill("navigate", "领航", 10),
    _skill("fighting_brawl", "格斗：", 25, specialization="斗殴"),
    _skill("drive_auto", "汽车驾驶", 20),
    _skill("fighting_1", "格斗①", 0, specialization_editable=True),
    _skill("ride", "骑术", 5),
    _skill("fighting_2", "格斗②", 0, specialization_editable=True),
    _skill("pilot", "驾驶：", 1, specialization_editable=True),
    _skill("fighting_3", "格斗③", 0, specialization_editable=True),
    _skill("survival", "生存：", 10, specialization_editable=True),
    _skill("firearms_handgun", "射击：", 20, specialization="手枪"),
    _skill("language_other_1", "外语①", 1, specialization_editable=True),
    _skill(
        "firearms_rifle_shotgun",
        "射击①",
        25,
        specialization="步枪/霍弹枪",
    ),
    _skill("language_other_2", "外语②", 1, specialization_editable=True),
    _skill("firearms_2", "射击②", 0, specialization_editable=True),
    _skill("language_other_3", "外语③", 1, specialization_editable=True),
    _skill("firearms_3", "射击③", 0, specialization_editable=True),
    _skill("language_own", "母语", 0, base_formula="edu"),
    _skill("first_aid", "急救", 30),
    _skill("diving", "潜水", 1),
    _skill("medicine", "医学", 1),
    _skill("demolitions", "爆破", 1),
    _skill("psychoanalysis", "精神分析", 1),
    _skill("read_lips", "读唇", 1),
    _skill("swim", "游泳", 20),
    _skill("hypnosis", "催眠", 1),
    _skill("climb", "攀爬", 20),
    _skill("artillery", "炮术", 1),
    _skill("throw", "投掷", 20),
    _skill("lore", "学问：", 1, specialization_editable=True),
    _skill("jump", "跳跃", 20),
    _skill("custom_skill", "自定义技能：", 1, specialization_editable=True),
    _skill("animal_handling", "驯兽", 5),
)


_SKILL_DESCRIPTIONS = {
    "credit_rating": "衡量财力、社会地位与获得金融便利的能力。",
    "occult": "识别神秘传统、仪式、民俗和象征，不等同于真实神话知识。",
    "cthulhu_mythos": "对克苏鲁神话真相的禁忌知识；创角时不能直接分配技能点。",
    "archaeology": "判断遗址、器物和古代文化的年代与用途。",
    "spot_hidden": "发现环境中的隐藏线索、异常或不起眼细节。",
    "history": "回忆历史人物、事件、地点与时代背景。",
    "listen": "听见、辨认或定位声音，包括偷听对话。",
    "natural_world": "识别常见动植物、自然现象与户外征兆。",
    "library_use": "在图书馆、档案和资料集中高效找到所需信息。",
    "anthropology": "理解人类文化、习俗、社会结构与群体行为。",
    "computer_use": "操作、编程或分析计算机系统；带 Ω 的现代技能需符合时代。",
    "science_1": "选择一门具体科学专攻，如化学、生物学或物理学。",
    "stealth": "在不被发现的情况下移动、躲藏或接近目标。",
    "science_2": "第二个独立科学专攻位，每个专攻分开计算。",
    "track": "追踪脚印、车辙、折断植被等行动痕迹。",
    "science_3": "第三个独立科学专攻位，每个专攻分开计算。",
    "disguise": "通过服装、化妆和举止改变身份或外观。",
    "electronics": "理解和维修电子设备；带 Ω 的现代技能需符合时代。",
    "locksmith": "开锁、制作钥匙、理解锁具及简单保险装置。",
    "law": "掌握法律、司法程序与当地法规。",
    "sleight_of_hand": "偷取、藏匿或悄然操作小型物件。",
    "accounting": "检查账本、资金流与财务异常。",
    "appraise": "估计物品的价值、品质、真伪或来历。",
    "art_craft_1": "选择一项具体技艺或工艺，如钢琴、摄影或木工。",
    "charm": "以亲和、讨喜或情感方式影响他人。",
    "art_craft_2": "第二个独立技艺或工艺专攻位。",
    "fast_talk": "用快速话术、误导或临时借口说服对方。",
    "art_craft_3": "第三个独立技艺或工艺专攻位。",
    "intimidate": "通过威胁、力量或恐惧迫使他人服从。",
    "electrical_repair": "安装或修复电路、电机和基础电气设备。",
    "persuade": "通过合理论证和长时间交流说服对方。",
    "mechanical_repair": "维修、制作或理解机械装置。",
    "psychology": "判断他人的情绪、动机、说谎或心理状态。",
    "operate_heavy_machinery": "操作推土机、起重机等大型机械。",
    "dodge": "躲避近战攻击或危险，基础值等于 DEX 的一半。",
    "navigate": "使用地图、星象或地标确定方向和路线。",
    "fighting_brawl": "使用拳脚、头撞或常见即兴武器近战。",
    "drive_auto": "驾驶汽车或其他常见地面机动车辆。",
    "fighting_1": "选择一种独立格斗专攻，如刀剑或绞索。",
    "ride": "骑乘、控制并照料马匹或其他骑乘动物。",
    "fighting_2": "第二个独立格斗专攻位。",
    "pilot": "选择一种船舶或飞行器驾驶专攻。",
    "fighting_3": "第三个独立格斗专攻位。",
    "survival": "在指定环境中寻找水食、庇护所并规避危险。",
    "firearms_handgun": "使用手枪和其他短管枪械。",
    "language_other_1": "选择一门非母语，表示理解、说和阅读的熟练度。",
    "firearms_rifle_shotgun": "使用步枪或霍弹枪类长枪。",
    "language_other_2": "第二门独立外语专攻位。",
    "firearms_2": "选择一种独立射击专攻，如冲锋枪或机枪。",
    "language_other_3": "第三门独立外语专攻位。",
    "firearms_3": "另一个独立射击专攻位。",
    "language_own": "调查员的母语能力，基础值等于 EDU。",
    "first_aid": "现场紧急止血、包扎和稳定伤势。",
    "diving": "使用潜水器材并在水下安全行动。",
    "medicine": "诊断、治疗疾病与伤势，通常需要时间和设备。",
    "demolitions": "识别、安放或拆除爆炸物。",
    "psychoanalysis": "长期治疗心理创伤或帮助缓解疯狂状态。",
    "read_lips": "通过观察口型理解无法听到的话语。",
    "swim": "在水中移动、漂浮和应对水流。",
    "hypnosis": "引导受试者进入催眠状态，通常受 KP 严格裁定。",
    "climb": "攀爬墙壁、山岩、绳索或其他陡峭表面。",
    "artillery": "操作大口径火炮、迫击炮或类似重武器。",
    "throw": "准确投掷物体或投掷武器。",
    "lore": "选择一门特定学问或地方性知识。",
    "jump": "完成远跳、高跳或跨越障碍。",
    "custom_skill": "为特定设定创建的自定义技能，名称和用途由 KP 确认。",
    "animal_handling": "训练、安抚或控制家养与常见动物。",
}


COC7_SKILL_CATALOG: tuple[SkillCatalogEntry, ...] = tuple(
    {
        "skill_key": f"coc7.{row[0]}",
        "display_name": row[1],
        "base_value": row[2],
        "default_specialization": row[3],
        "base_formula": row[4],
        "specialization_editable": row[5],
        "creation_points_allowed": row[6],
        "description": _SKILL_DESCRIPTIONS[row[0]],
        "order": order,
    }
    for order, row in enumerate(_SKILL_ROWS, start=1)
)


def list_coc7_skill_catalog() -> list[SkillCatalogEntry]:
    return [dict(entry) for entry in COC7_SKILL_CATALOG]
