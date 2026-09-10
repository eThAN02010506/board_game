"""Bounded, review-safe audit notes for scenario authoring transforms."""

from __future__ import annotations

from collections.abc import Iterable

from ai_kp.platform.resolution.scenario_ending_supplement import (
    SERVER_COVERAGE_ENDING_ASSUMPTION_PREFIX,
)
from ai_kp.platform.resolution.scenario_supplement import (
    SERVER_ACTION_GOAL_BOUNDARY_ASSUMPTION_PREFIX,
    SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX,
)

_SERVER_REVIEW_DIAGNOSTIC_PREFIXES = (
    SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX,
    SERVER_COVERAGE_ENDING_ASSUMPTION_PREFIX,
    "Deterministic IR transport normalization applied:",
    "Ruleset outcome-pair notation was split",
    "Source partitions proposed conflicting",
    "Unknown initial location was removed from entity ",
    "Unknown proposed initial scene was discarded:",
    "Unknown optional response obligations were removed from action ",
    "Action with unknown location slot was discarded:",
    "Action with conflicting location scopes was discarded:",
    "Action with unknown or non-exact scene condition was discarded:",
    "Action with unknown location id was discarded:",
    "Action location was not anchored by its cited source and was discarded:",
    "Evidence-backed action without a location scope was discarded:",
    "Source-unsupported global action was discarded:",
    "Coverage action without one uniquely source-anchored external location was discarded:",
    "Outcome-claiming public setup was removed",
    "Core clue without a provable discovery route was discarded:",
    "Unreferenced no-op action was discarded:",
    "Location link with unknown endpoint was discarded:",
    "The source corpus exceeded the bounded authoring window.",
    "Unsupported authoring assumptions were removed after independent review:",
    "Server fail-forward pressure applied:",
    "Server automatic clue state aligned:",
    "Server source-authorized automatic clue aligned:",
    "Server outcome-bound clue narration aligned:",
    "Server unreachable location contracted:",
    "Server premature ending contracted:",
    "Server source-explicit location link materialized:",
    "Server source scene locations materialized:",
    "Server canonical travel conflict contracted:",
    "Source did not prove a playable location role; IR location was discarded:",
    "Source-unproven playable location contracted:",
    "Format-equivalent locations merged as ",
    "Format-equivalent locations were kept separate across source parents:",
    "Conflicting location occurrences outside the unique source identity were discarded:",
    "Location ids with conflicting source identities were discarded:",
    "Location with conflicting ",
    "Location link with a conflicting identity was discarded:",
    "Location link collapsed to itself and was discarded:",
    SERVER_ACTION_GOAL_BOUNDARY_ASSUMPTION_PREFIX,
)


def is_server_review_diagnostic(value: str) -> bool:
    """Return whether a note records a deterministic server transform."""

    if value.startswith(_SERVER_REVIEW_DIAGNOSTIC_PREFIXES):
        return True
    if value.startswith("Command ") and (
        " with an unknown or forbidden target was discarded from " in value
    ):
        return True
    if value.startswith("Ending ") and (
        " with unproduced state paths was discarded:" in value
    ):
        return True
    return value.startswith("Action ") and (
        " has an unresolved abstract check: " in value
        or " proposed unknown or ambiguous check key: " in value
        or " source-absent skill choices were discarded: " in value
        or value.endswith(
            (
                " without a source-provable skill choice was discarded.",
                " proposed a check without a ruleset skill choice.",
            )
        )
    )


def bounded_authoring_assumptions(
    *groups: Iterable[str],
    limit: int = 64,
) -> tuple[str, ...]:
    """Keep deterministic audit facts ahead of model-authored assumptions."""

    unique = tuple(
        dict.fromkeys(item for group in groups for item in group if item.strip())
    )
    server = tuple(item for item in unique if is_server_review_diagnostic(item))
    authored = tuple(item for item in unique if not is_server_review_diagnostic(item))
    if len(server) >= limit:
        # Later server transforms observe and narrow the output of earlier
        # transforms, so their audit facts describe the final authority state.
        return server[-limit:]
    return (*server, *authored)[:limit]


__all__ = ["bounded_authoring_assumptions", "is_server_review_diagnostic"]
