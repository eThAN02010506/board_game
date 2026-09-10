from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from ai_kp.platform.randomness import (
    DiceComponent,
    DiceRollRequest,
    DiceRollResult,
)
from ai_kp.platform.resolution.check_catalog import ScenarioCheckCatalog
from ai_kp.platform.resolution.effect_catalog import ScenarioEffectCatalog
from ai_kp.rulesets.base import (
    RulesetEffectFollowUp,
    RulesetEffectTransition,
    RulesetManifest,
)
from ai_kp.rulesets.coc7.character.pipeline import (
    normalize_character_sheet,
    validate_character_sheet,
)
from ai_kp.rulesets.coc7.character.recommendations import recommend_coc7_skill_points
from ai_kp.rulesets.coc7.character.skills import list_coc7_skill_catalog
from ai_kp.rulesets.coc7.character.xlsx_import import import_coc_character_xlsx
from ai_kp.rulesets.coc7.mechanics.opposed_check import (
    OpposedParticipant,
    resolve_opposed,
)
from ai_kp.rulesets.coc7.mechanics.skill_check import (
    RULESET_ID,
    RULESET_VERSION,
    SOURCE_REFERENCE,
    SuccessLevel,
    resolve_d100,
)
from ai_kp.rulesets.coc7.scenario_checks import coc7_scenario_check_catalog
from ai_kp.rulesets.coc7.scenario_effects import coc7_scenario_effect_catalog
from ai_kp.rulesets.sdk.characters import CharacterSheetValidation


class Coc7Ruleset:
    """The only executable ruleset currently installed by AI KP Local."""

    manifest = RulesetManifest(
        contract_version="1.0.0",
        ruleset_id=RULESET_ID,
        version=RULESET_VERSION,
        slug="coc7",
        display_name="克苏鲁的呼唤 第七版",
        engine_family="basic-roleplaying-percentile",
        source_version="CoC 7e / Keeper Rulebook CN 2002c",
        aliases=("coc", "call-of-cthulhu-7e"),
        character_schema_version="coc7-investigator-v1",
        event_schema_version="coc7-event-v1",
        knowledge_namespace="rulesets/coc7/2002c",
        supported_locales=("zh-CN",),
        support_level="playable_alpha",
        source_reference=SOURCE_REFERENCE,
        license={
            "id": "user-supplied-proprietary-reference",
            "content_scope": (
                "Local deterministic implementation with user-supplied reference; "
                "official rule text, setting, art, and trade dress are not bundled "
                "for redistribution."
            ),
            "attribution": "Call of Cthulhu is a trademark of Chaosium Inc.",
        },
        capabilities=(
            "character_sheet",
            "xlsx_import",
            "skill_catalog",
            "skill_recommendation",
            "skill_check",
            "opposed_check",
            "combat_state_machine",
            "damage_and_healing",
            "sanity_state_machine",
            "chase_state_machine",
            "development_phase",
        ),
        map_modes=("none", "zone", "square_grid", "freeform"),
        ui_slots=("character_sheet", "check_panel", "encounter_panel"),
    )

    def normalize_character_sheet(
        self, sheet: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        return normalize_character_sheet(sheet)

    def validate_character_sheet(
        self, sheet: dict[str, Any]
    ) -> CharacterSheetValidation:
        return validate_character_sheet(sheet)

    def import_character_xlsx(self, data: bytes, filename: str) -> dict[str, Any]:
        return import_coc_character_xlsx(data, filename)

    def list_skill_catalog(self) -> list[dict[str, Any]]:
        return list_coc7_skill_catalog()

    def scenario_check_catalog(self) -> ScenarioCheckCatalog:
        return coc7_scenario_check_catalog()

    def scenario_effect_catalog(self) -> ScenarioEffectCatalog:
        return coc7_scenario_effect_catalog()

    def translate_scenario_effect(
        self, effect_key: str, payload: Mapping[str, Any]
    ) -> RulesetEffectTransition:
        errors = self.scenario_effect_catalog().validate_effect(
            effect_key, dict(payload)
        )
        if errors:
            raise ValueError("Invalid CoC7 scenario effect: " + "; ".join(errors))
        if effect_key == "san_loss":
            return RulesetEffectTransition(
                command_type="san_loss",
                payload={"loss_expression": payload["loss"]},
            )
        if effect_key == "damage":
            return RulesetEffectTransition(
                command_type="damage",
                payload={"damage_expression": payload["damage"]},
                follow_ups=(
                    RulesetEffectFollowUp(
                        result_flag="requires_major_wound_con_check",
                        command_type="major_wound_con",
                        payload={},
                    ),
                    RulesetEffectFollowUp(
                        result_flag="requires_dying_con_check",
                        command_type="dying_con",
                        payload={},
                    ),
                ),
            )
        resource_effects = {
            "san_restore": ("san", "gain"),
            "mp_gain": ("mp", "gain"),
            "mp_loss": ("mp", "loss"),
            "luck_gain": ("luck", "gain"),
            "luck_reduction": ("luck", "loss"),
        }
        if effect_key in resource_effects:
            resource, direction = resource_effects[effect_key]
            return RulesetEffectTransition(
                command_type="resource_adjust",
                payload={
                    "resource": resource,
                    "direction": direction,
                    "amount_expression": payload["amount"],
                },
            )
        if effect_key in {"armor_gain", "armor_reduction"}:
            return RulesetEffectTransition(
                command_type="armor_adjust",
                payload={
                    "direction": (
                        "gain" if effect_key == "armor_gain" else "loss"
                    ),
                    "amount_expression": payload["amount"],
                },
            )
        if effect_key in {"skill_gain", "skill_reduction"}:
            return RulesetEffectTransition(
                command_type="skill_adjust",
                payload={
                    "skill_key": payload["skill_key"],
                    "direction": (
                        "gain" if effect_key == "skill_gain" else "loss"
                    ),
                    "amount_expression": payload["amount"],
                },
            )
        raise ValueError(f"Unsupported CoC7 scenario effect: {effect_key}")

    def recommend_skill_points(self, **payload: Any) -> dict[str, Any]:
        return recommend_coc7_skill_points(**payload)

    def validate_check(
        self, *, target: int | None, difficulty: str, bonus_dice: int
    ) -> None:
        if target is not None and not 0 <= target <= 100:
            raise ValueError("Check target must be between 0 and 100")
        if difficulty not in {"regular", "hard", "extreme"}:
            raise ValueError("Unsupported CoC7 check difficulty")
        if not -2 <= bonus_dice <= 2:
            raise ValueError("CoC7 bonus dice must be between -2 and 2")

    def build_check_roll_request(self, bonus_dice: int) -> DiceRollRequest:
        self.validate_check(target=None, difficulty="regular", bonus_dice=bonus_dice)
        return DiceRollRequest(
            components=(
                DiceComponent(key="ones_digit", count=1, sides=10, minimum=0),
                DiceComponent(
                    key="tens_digits",
                    count=abs(bonus_dice) + 1,
                    sides=10,
                    minimum=0,
                ),
            )
        )

    def build_physical_check_roll(
        self,
        *,
        bonus_dice: int,
        values: Mapping[str, Any],
    ) -> DiceRollResult:
        request = self.build_check_roll_request(bonus_dice)
        ones_digit = values.get("ones_digit")
        tens_digits = values.get("tens_digits")
        if ones_digit is None:
            raise ValueError("Physical dice require the ones digit")
        if not isinstance(tens_digits, (list, tuple)):
            raise TypeError("Physical dice require tens digits")
        return DiceRollResult.create(
            request,
            {
                "ones_digit": (int(ones_digit),),
                "tens_digits": tuple(int(value) for value in tens_digits),
            },
        )

    def check_dice_from_roll(self, roll: DiceRollResult) -> dict[str, Any]:
        keys = {component.key for component in roll.request.components}
        if keys != {"ones_digit", "tens_digits"}:
            raise ValueError("CoC7 checks require percentile digit evidence")
        return {
            "ones_digit": int(roll.rolls["ones_digit"][0]),
            "tens_digits": [int(value) for value in roll.rolls["tens_digits"]],
        }

    def resolve_check(
        self,
        *,
        target: int,
        difficulty: str,
        bonus_dice: int,
        raw_dice: Mapping[str, Any],
    ) -> dict[str, Any]:
        return resolve_d100(
            target=target,
            difficulty=difficulty,
            bonus_dice=bonus_dice,
            ones_digit=int(raw_dice["ones_digit"]),
            tens_digits=tuple(int(item) for item in raw_dice["tens_digits"]),
        ).as_dict()

    def resolve_opposed_check(
        self,
        *,
        left_participant_id: str,
        left_target: int,
        left_roll: int,
        right_participant_id: str,
        right_target: int,
        right_roll: int,
        left_success_level: str | None = None,
        right_success_level: str | None = None,
    ) -> dict[str, Any]:
        return resolve_opposed(
            OpposedParticipant(
                left_participant_id,
                left_target,
                left_roll,
                cast(SuccessLevel | None, left_success_level),
            ),
            OpposedParticipant(
                right_participant_id,
                right_target,
                right_roll,
                cast(SuccessLevel | None, right_success_level),
            ),
        ).as_dict()


COC7_RULESET = Coc7Ruleset()


__all__ = ["COC7_RULESET", "Coc7Ruleset"]
