from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class RulesetManifest:
    """Stable metadata exposed to campaigns, stored checks, and the UI."""

    ruleset_id: str
    version: str
    slug: str
    display_name: str
    aliases: tuple[str, ...]
    character_schema_version: str
    source_reference: Mapping[str, Any]
    capabilities: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["aliases"] = list(self.aliases)
        result["capabilities"] = list(self.capabilities)
        result["source_reference"] = dict(self.source_reference)
        return result


class Ruleset(Protocol):
    """Narrow port used by application services.

    The current public check payload remains CoC-shaped. A future non-CoC plugin
    can extend the port only when its real rulebook and acceptance cases exist.
    """

    manifest: RulesetManifest

    def normalize_character_sheet(
        self, sheet: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]: ...

    def import_character_xlsx(self, data: bytes, filename: str) -> dict[str, Any]: ...

    def list_skill_catalog(self) -> list[dict[str, Any]]: ...

    def recommend_skill_points(self, **payload: Any) -> dict[str, Any]: ...

    def validate_check(self, *, target: int | None, difficulty: str, bonus_dice: int) -> None: ...

    def generate_check_dice(self, bonus_dice: int) -> dict[str, Any]: ...

    def resolve_check(
        self,
        *,
        target: int,
        difficulty: str,
        bonus_dice: int,
        raw_dice: Mapping[str, Any],
    ) -> dict[str, Any]: ...


__all__ = ["Ruleset", "RulesetManifest"]
