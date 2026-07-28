from __future__ import annotations

from typing import Any, Mapping

from ai_kp.rulesets.coc7.character.recommendations import recommend_coc7_skill_points
from ai_kp.rulesets.coc7.character.skills import list_coc7_skill_catalog
from ai_kp.rulesets.coc7.character.pipeline import (
    normalize_character_sheet,
    validate_character_sheet,
)
from ai_kp.rulesets.coc7.character.xlsx_import import import_coc_character_xlsx
from ai_kp.rulesets.coc7.mechanics.opposed_check import (
    OpposedParticipant,
    resolve_opposed,
)
from ai_kp.rulesets.coc7.mechanics.skill_check import (
    RULESET_ID,
    RULESET_VERSION,
    SOURCE_REFERENCE,
    resolve_d100,
    secure_d100_dice,
)
from ai_kp.rulesets.base import RulesetManifest
from ai_kp.rulesets.sdk.characters import CharacterSheetValidation


class Coc7Ruleset:
    """The only executable ruleset currently installed by AI KP Local."""

    manifest = RulesetManifest(
        ruleset_id=RULESET_ID,
        version=RULESET_VERSION,
        slug="coc7",
        display_name="克苏鲁的呼唤 第七版",
        aliases=("coc", "call-of-cthulhu-7e"),
        character_schema_version="coc7-investigator-v1",
        source_reference=SOURCE_REFERENCE,
        capabilities=(
            "character_sheet",
            "xlsx_import",
            "skill_catalog",
            "skill_recommendation",
            "skill_check",
            "opposed_check",
        ),
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

    def generate_check_dice(self, bonus_dice: int) -> dict[str, Any]:
        ones_digit, tens_digits = secure_d100_dice(bonus_dice)
        return {"ones_digit": ones_digit, "tens_digits": list(tens_digits)}

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
    ) -> dict[str, Any]:
        return resolve_opposed(
            OpposedParticipant(left_participant_id, left_target, left_roll),
            OpposedParticipant(right_participant_id, right_target, right_roll),
        ).as_dict()


COC7_RULESET = Coc7Ruleset()


__all__ = ["COC7_RULESET", "Coc7Ruleset"]
