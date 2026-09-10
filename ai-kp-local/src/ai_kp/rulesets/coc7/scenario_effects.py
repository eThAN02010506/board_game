"""Scenario-authorized CoC7 character effects."""

from ai_kp.platform.resolution.effect_catalog import (
    ScenarioEffectCatalog,
    ScenarioEffectCatalogEntry,
    ScenarioEffectParameter,
)


def _dice_amount(key: str = "amount") -> ScenarioEffectParameter:
    return ScenarioEffectParameter(key=key, value_type="dice_expression")


def coc7_scenario_effect_catalog() -> ScenarioEffectCatalog:
    return ScenarioEffectCatalog(
        ruleset_id="coc7-keeper-cn-2002c",
        ruleset_aliases=("coc7", "coc"),
        entries=(
            ScenarioEffectCatalogEntry(
                effect_key="damage",
                display_name="生命值伤害",
                source_aliases=("攻击造成", "伤害", "生命值损失", "HP损失"),
                source_preceding_blockers=("护甲减少", "护甲降低", "护甲减免"),
                parameters=(
                    ScenarioEffectParameter(
                        key="damage",
                        value_type="dice_expression",
                        source_additive_term_aliases=("伤害加值", "damage bonus", "DB"),
                    ),
                ),
            ),
            ScenarioEffectCatalogEntry(
                effect_key="san_loss",
                display_name="理智损失",
                source_aliases=("SAN", "理智损失", "理智减少"),
                source_preceding_blockers=("恢复", "增加", "提升", "获得"),
                source_following_blockers=("恢复", "增加", "提升", "获得"),
                parameters=(
                    ScenarioEffectParameter(
                        key="loss",
                        value_type="dice_expression",
                        outcome_pair_separator="/",
                    ),
                ),
            ),
            ScenarioEffectCatalogEntry(
                effect_key="san_restore",
                display_name="恢复理智值",
                source_aliases=("SAN恢复", "恢复SAN", "理智恢复", "恢复理智"),
                parameters=(_dice_amount(),),
            ),
            ScenarioEffectCatalogEntry(
                effect_key="mp_gain",
                display_name="恢复魔法值",
                source_aliases=("MP恢复", "恢复MP", "魔法值恢复", "恢复魔法值"),
                parameters=(_dice_amount(),),
            ),
            ScenarioEffectCatalogEntry(
                effect_key="mp_loss",
                display_name="损失魔法值",
                source_aliases=("MP损失", "消耗MP", "魔法值损失", "消耗魔法值"),
                parameters=(_dice_amount(),),
            ),
            ScenarioEffectCatalogEntry(
                effect_key="luck_gain",
                display_name="获得幸运值",
                source_aliases=("幸运增加", "幸运提升", "获得幸运"),
                parameters=(_dice_amount(),),
            ),
            ScenarioEffectCatalogEntry(
                effect_key="luck_reduction",
                display_name="失去幸运值",
                source_aliases=("幸运减少", "幸运降低", "失去幸运"),
                parameters=(_dice_amount(),),
            ),
            ScenarioEffectCatalogEntry(
                effect_key="armor_gain",
                display_name="获得护甲",
                source_aliases=("获得护甲", "护甲增加", "护甲提升"),
                parameters=(_dice_amount(),),
            ),
            ScenarioEffectCatalogEntry(
                effect_key="armor_reduction",
                display_name="护甲降低",
                source_aliases=("护甲减少", "护甲降低", "护甲值降低", "失去护甲"),
                source_following_blockers=("伤害",),
                parameters=(_dice_amount(),),
            ),
            ScenarioEffectCatalogEntry(
                effect_key="skill_gain",
                display_name="技能增加",
                source_aliases=("技能增加", "技能提升"),
                parameters=(
                    ScenarioEffectParameter(key="skill_key", value_type="string"),
                    _dice_amount(),
                ),
            ),
            ScenarioEffectCatalogEntry(
                effect_key="skill_reduction",
                display_name="技能降低",
                source_aliases=("技能减少", "技能降低"),
                parameters=(
                    ScenarioEffectParameter(key="skill_key", value_type="string"),
                    _dice_amount(),
                ),
            ),
        ),
    )


__all__ = ["coc7_scenario_effect_catalog"]
