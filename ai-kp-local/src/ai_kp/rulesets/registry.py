from __future__ import annotations

from collections.abc import Iterable

from ai_kp.rulesets.base import Ruleset
from ai_kp.rulesets.coc7 import COC7_RULESET

DEFAULT_RULESET_ID = "coc7"


class RulesetRegistry:
    """Explicit allow-list of rule engines shipped with this local server."""

    def __init__(self, rulesets: Iterable[Ruleset]):
        self._rulesets: dict[str, Ruleset] = {}
        self._canonical: dict[str, Ruleset] = {}
        for ruleset in rulesets:
            manifest = ruleset.manifest
            if manifest.ruleset_id in self._canonical:
                raise ValueError(f"Duplicate ruleset id: {manifest.ruleset_id}")
            self._canonical[manifest.ruleset_id] = ruleset
            for name in {manifest.ruleset_id, manifest.slug, *manifest.aliases}:
                normalized = name.strip().lower()
                if normalized in self._rulesets:
                    raise ValueError(f"Duplicate ruleset alias: {name}")
                self._rulesets[normalized] = ruleset

    def get(self, identifier: str | None = None) -> Ruleset:
        normalized = (identifier or DEFAULT_RULESET_ID).strip().lower()
        try:
            return self._rulesets[normalized]
        except KeyError as exc:
            raise ValueError(
                f"Ruleset is not installed: {identifier}. "
                "Uploading a rulebook does not install an executable ruleset."
            ) from exc

    def list(self) -> list[dict]:
        return [
            ruleset.manifest.as_dict()
            for ruleset in sorted(
                self._canonical.values(), key=lambda item: item.manifest.slug
            )
        ]


registry = RulesetRegistry([COC7_RULESET])


def get_ruleset(identifier: str | None = None) -> Ruleset:
    return registry.get(identifier)


def list_rulesets() -> list[dict]:
    return registry.list()


__all__ = [
    "DEFAULT_RULESET_ID",
    "RulesetRegistry",
    "get_ruleset",
    "list_rulesets",
    "registry",
]
