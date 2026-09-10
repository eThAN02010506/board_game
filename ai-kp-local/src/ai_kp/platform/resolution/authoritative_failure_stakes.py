"""Project validated failure commands into safe, player-visible stakes."""

from __future__ import annotations

from ai_kp.platform.resolution.contracts import WorldCommand
from ai_kp.platform.resolution.effect_catalog import (
    ScenarioEffectCatalog,
    ScenarioEffectCatalogEntry,
)
from ai_kp.platform.resolution.source_outcomes import (
    is_action_goal_boundary_command,
    unspecified_outcome_summary,
)


def authoritative_failure_stakes(
    *,
    action_id: str,
    action_title: str,
    commands: tuple[WorldCommand, ...],
    ruleset_id: str,
    effect_catalog: ScenarioEffectCatalog | None,
) -> str:
    """Return only consequences proven by closed server commands.

    Only the server-owned action-goal boundary is private bookkeeping for an
    unmet goal. Ruleset effects are safe to name only after the installed catalog
    validates both their key and payload. Any other command (especially an opaque
    event or arbitrary fact mutation) makes the fallback unavailable;
    source-authored prose may still describe such a branch.
    """

    if not commands:
        return ""

    effects: list[tuple[ScenarioEffectCatalogEntry, WorldCommand]] = []
    for command in commands:
        if is_action_goal_boundary_command(
            command,
            action_id=action_id,
            outcome="failure",
        ):
            continue
        if command.kind != "apply_ruleset_effect":
            return ""
        if (
            effect_catalog is None
            or not effect_catalog.supports(ruleset_id)
            or command.actor_id not in {None, "$actor"}
            or effect_catalog.validate_effect(
                str(command.event_type), command.payload
            )
        ):
            return ""
        entry = next(
            item
            for item in effect_catalog.entries
            if item.effect_key == command.event_type
        )
        effects.append((entry, command))

    if not effects:
        return unspecified_outcome_summary(action_title, "failure")

    consequences = "；".join(
        _public_effect_summary(entry, command) for entry, command in effects
    )
    return f"失败将承受该分支已冻结的规则后果：{consequences}。"


def _public_effect_summary(
    entry: ScenarioEffectCatalogEntry,
    command: WorldCommand,
) -> str:
    """Render consent-relevant magnitudes without leaking protocol identifiers."""

    visible_values = [
        str(command.payload[parameter.key])
        for parameter in entry.parameters
        if parameter.key in command.payload
        and parameter.value_type in {"dice_expression", "integer"}
    ]
    if not visible_values:
        return entry.display_name
    return f"{entry.display_name}（{'，'.join(visible_values)}）"


__all__ = ["authoritative_failure_stakes"]
