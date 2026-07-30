"""Installed tabletop rulesets and their stable application-facing registry."""

from ai_kp.rulesets.registry import (
    DEFAULT_RULESET_ID,
    get_campaign_ruleset,
    get_ruleset,
    list_rulesets,
)

__all__ = [
    "DEFAULT_RULESET_ID",
    "get_campaign_ruleset",
    "get_ruleset",
    "list_rulesets",
]
