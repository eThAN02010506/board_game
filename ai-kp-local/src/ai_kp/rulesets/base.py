from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol

from ai_kp.platform.randomness import DiceRollRequest, DiceRollResult
from ai_kp.platform.resolution.check_catalog import ScenarioCheckCatalog
from ai_kp.platform.resolution.effect_catalog import ScenarioEffectCatalog
from ai_kp.rulesets.sdk.characters import CharacterSheetValidation

RulesetSupportLevel = Literal[
    "knowledge_only",
    "assisted",
    "playable_alpha",
    "verified_playable",
]
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _validate_string_collections(
    *,
    required: Mapping[str, tuple[str, ...]],
    optional: Mapping[str, tuple[str, ...]],
) -> None:
    for name, values in required.items():
        if not values:
            raise ValueError(f"Ruleset manifest {name} must not be empty")
    for name, values in {**required, **optional}.items():
        if any(not value.strip() for value in values):
            raise ValueError(f"Ruleset manifest {name} must contain non-empty values")
        if len(values) != len(set(values)):
            raise ValueError(f"Ruleset manifest {name} contains duplicates")


@dataclass(frozen=True)
class RulesetManifest:
    """Stable metadata exposed to campaigns, stored checks, and the UI."""

    contract_version: str
    ruleset_id: str
    version: str
    slug: str
    display_name: str
    engine_family: str
    source_version: str
    aliases: tuple[str, ...]
    character_schema_version: str
    event_schema_version: str
    knowledge_namespace: str
    supported_locales: tuple[str, ...]
    support_level: RulesetSupportLevel
    source_reference: Mapping[str, Any]
    license: Mapping[str, Any]
    capabilities: tuple[str, ...]
    map_modes: tuple[str, ...]
    ui_slots: tuple[str, ...]

    def __post_init__(self) -> None:
        required = {
            "contract_version": self.contract_version,
            "ruleset_id": self.ruleset_id,
            "version": self.version,
            "slug": self.slug,
            "display_name": self.display_name,
            "engine_family": self.engine_family,
            "source_version": self.source_version,
            "character_schema_version": self.character_schema_version,
            "event_schema_version": self.event_schema_version,
            "knowledge_namespace": self.knowledge_namespace,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(f"Ruleset manifest fields are required: {', '.join(missing)}")
        if not _SEMVER_PATTERN.fullmatch(self.contract_version):
            raise ValueError("Ruleset manifest contract_version must be valid SemVer")
        if self.support_level not in {
            "knowledge_only",
            "assisted",
            "playable_alpha",
            "verified_playable",
        }:
            raise ValueError(f"Unsupported ruleset support level: {self.support_level}")
        _validate_string_collections(
            required={
                "supported_locales": self.supported_locales,
                "capabilities": self.capabilities,
            },
            optional={
                "aliases": self.aliases,
                "map_modes": self.map_modes,
                "ui_slots": self.ui_slots,
            },
        )
        if not self.source_reference:
            raise ValueError("Ruleset manifest source_reference is required")
        if not self.license.get("id") or not self.license.get("content_scope"):
            raise ValueError("Ruleset manifest license id and content_scope are required")

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for name in (
            "aliases",
            "supported_locales",
            "capabilities",
            "map_modes",
            "ui_slots",
        ):
            result[name] = list(getattr(self, name))
        result["source_reference"] = dict(self.source_reference)
        result["license"] = dict(self.license)
        return result


@dataclass(frozen=True)
class RulesetEffectFollowUp:
    """Plugin-owned conditional transition after the primary effect result."""

    result_flag: str
    command_type: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class RulesetEffectTransition:
    """Plugin-selected character transition for one validated scenario effect."""

    command_type: str
    payload: Mapping[str, Any]
    follow_ups: tuple[RulesetEffectFollowUp, ...] = ()


class Ruleset(Protocol):
    """Narrow port used by application services.

    Ruleset-neutral conversation framing happens before this port. The current
    public mechanical check payload remains CoC-shaped because CoC7 is the only
    executable plugin; a future system must bring real rulebook acceptance cases
    before the mechanical port is generalized around its actual differences.
    """

    manifest: RulesetManifest

    def normalize_character_sheet(
        self, sheet: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]: ...

    def validate_character_sheet(
        self, sheet: dict[str, Any]
    ) -> CharacterSheetValidation: ...

    def import_character_xlsx(self, data: bytes, filename: str) -> dict[str, Any]: ...

    def list_skill_catalog(self) -> list[dict[str, Any]]: ...

    def scenario_check_catalog(self) -> ScenarioCheckCatalog: ...

    def scenario_effect_catalog(self) -> ScenarioEffectCatalog: ...

    def translate_scenario_effect(
        self, effect_key: str, payload: Mapping[str, Any]
    ) -> RulesetEffectTransition: ...

    def recommend_skill_points(self, **payload: Any) -> dict[str, Any]: ...

    def validate_check(self, *, target: int | None, difficulty: str, bonus_dice: int) -> None: ...

    def build_check_roll_request(self, bonus_dice: int) -> DiceRollRequest: ...

    def build_physical_check_roll(
        self,
        *,
        bonus_dice: int,
        values: Mapping[str, Any],
    ) -> DiceRollResult: ...

    def check_dice_from_roll(self, roll: DiceRollResult) -> dict[str, Any]: ...

    def resolve_check(
        self,
        *,
        target: int,
        difficulty: str,
        bonus_dice: int,
        raw_dice: Mapping[str, Any],
    ) -> dict[str, Any]: ...

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
    ) -> dict[str, Any]: ...


__all__ = [
    "Ruleset",
    "RulesetEffectFollowUp",
    "RulesetEffectTransition",
    "RulesetManifest",
    "RulesetSupportLevel",
]
