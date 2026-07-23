"""Three-layer CoC7 investigator validation pipeline."""

from typing import Any

from ai_kp.rulesets.coc7.character.creation_rules import (
    validate_creation_rules,
    validate_review_policy,
    validate_structure,
)
from ai_kp.rulesets.coc7.character.normalization import canonicalize_character_sheet
from ai_kp.rulesets.sdk.characters import (
    CharacterSheetValidation,
    CharacterValidationReport,
)


def validate_character_sheet(sheet: dict[str, Any]) -> CharacterSheetValidation:
    canonical = canonicalize_character_sheet(sheet)
    issues = (
        *validate_structure(sheet, canonical),
        *validate_creation_rules(canonical),
        *validate_review_policy(canonical),
    )
    return CharacterSheetValidation(
        canonical_sheet=canonical,
        report=CharacterValidationReport(issues=issues),
    )


def normalize_character_sheet(
    sheet: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Backward-compatible tuple result for existing callers."""

    result = validate_character_sheet(sheet)
    return result.canonical_sheet, result.report.warnings
