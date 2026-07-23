"""Compatibility exports for the split CoC7 character validation pipeline."""

from ai_kp.rulesets.coc7.character.creation_rules import OCCUPATION_POINT_FORMULAS
from ai_kp.rulesets.coc7.character.normalization import ATTRIBUTE_KEYS
from ai_kp.rulesets.coc7.character.pipeline import (
    normalize_character_sheet,
    validate_character_sheet,
)

__all__ = [
    "ATTRIBUTE_KEYS",
    "OCCUPATION_POINT_FORMULAS",
    "normalize_character_sheet",
    "validate_character_sheet",
]
