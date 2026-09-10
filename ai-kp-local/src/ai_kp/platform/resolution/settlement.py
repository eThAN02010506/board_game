"""Pure command projection shared by sequential and parallel settlement."""

from __future__ import annotations

from ai_kp.platform.resolution.contracts import ResolutionPreview, WorldCommand


def resolved_action_commands(
    preview: ResolutionPreview,
    outcome: str,
    *,
    actor_id: str | None,
) -> tuple[WorldCommand, ...]:
    """Bind one exact outcome to its audit event and authoritative commands."""

    outcome_commands = preview.commands_for_outcome(outcome)
    payload = {
        "action_id": preview.action_id,
        "operator_id": preview.operator_id,
        "outcome": outcome,
    }
    normalized_actor_id = str(actor_id or "").strip()
    if normalized_actor_id:
        payload["actor_id"] = normalized_actor_id
    return (
        WorldCommand(
            kind="emit_event",
            event_type="action_resolved",
            payload=payload,
        ),
        *outcome_commands,
    )


__all__ = ["resolved_action_commands"]
