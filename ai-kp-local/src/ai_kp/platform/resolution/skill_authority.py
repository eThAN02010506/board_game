"""Ruleset-vocabulary parsing that grants no action or world authority."""

from __future__ import annotations

import re

from ai_kp.platform.resolution.check_catalog import ScenarioCheckCatalog

_EXPLICIT_SKILL = re.compile(
    r"(?:明确(?:选择|使用)|选择|使用|用)《?([A-Za-z0-9_\u3400-\u9fff：:]+?)》?"
    r"(?:技能)?(?:进行)?(?:检定|判定)"
)


def player_authorized_skill_keys(
    catalog: ScenarioCheckCatalog,
    player_intent: str,
) -> tuple[str, ...]:
    """Resolve explicit choices first, then ruleset-owned language aliases."""

    explicit: list[str] = []
    for match in _EXPLICIT_SKILL.finditer(player_intent):
        explicit.extend(catalog.resolve(match.group(1)))
    if explicit:
        return tuple(dict.fromkeys(explicit))
    direct = catalog.source_present_keys(player_intent)
    if direct:
        return direct
    conceptual = tuple(
        entry.check_key
        for entry in catalog.entries
        if any(alias and alias in player_intent for alias in entry.concept_aliases)
    )
    return tuple(dict.fromkeys(conceptual))


__all__ = ["player_authorized_skill_keys"]
