"""Player-safe projection for tabletop routing and per-entity Actor traces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from ai_kp.platform.agents.entity_actor import ActorExecutionTrace


def project_actor_execution_traces(value: object) -> list[dict[str, Any]]:
    """Return only the stable public trace allowlist; malformed rows fail closed."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    projected: list[dict[str, Any]] = []
    for raw in value[:8]:
        if not isinstance(raw, Mapping):
            continue
        allowlisted = {
            key: raw.get(key)
            for key in (
                "schema_version",
                "entity_id",
                "entity_title",
                "execution",
                "generation_attempt_count",
                "error_codes",
            )
        }
        try:
            trace = ActorExecutionTrace.model_validate(allowlisted)
        except ValidationError:
            continue
        projected.append(trace.model_dump(mode="json"))
    return projected


def project_player_tabletop_turn(value: object) -> dict[str, Any] | None:
    """Strip model diagnostics and authority bindings from a player tabletop view."""

    if not isinstance(value, Mapping):
        return None
    frame = value.get("frame")
    if not isinstance(frame, Mapping):
        return None
    safe_frame: dict[str, Any] = {
        key: frame.get(key)
        for key in (
            "kind",
            "goal",
            "method",
            "target_entity_ids",
            "dialogue",
            "steps",
            "time_span",
            "confidence",
        )
    }
    ambiguity = frame.get("ambiguity")
    safe_frame["ambiguity"] = (
        {
            key: ambiguity.get(key)
            for key in ("field", "question", "why_material")
        }
        if isinstance(ambiguity, Mapping)
        else None
    )
    response = value.get("response")
    safe_response = None
    if isinstance(response, Mapping):
        safe_response = {
            key: response.get(key)
            for key in (
                "public_narration",
                "speaker_entity_ids",
                "source",
                "attempt_count",
            )
        }
        safe_response["actor_traces"] = project_actor_execution_traces(
            response.get("actor_traces")
        )
    return {
        "schema_version": value.get("schema_version"),
        "route": value.get("route"),
        "attempt_count": value.get("attempt_count"),
        "audit_count": value.get("audit_count"),
        "frame": safe_frame,
        "response": safe_response,
    }


def with_player_safe_tabletop(adjudication: Mapping[str, Any]) -> dict[str, Any]:
    """Copy an adjudication while replacing its tabletop payload with a safe view."""

    projected = dict(adjudication)
    projected["tabletop_turn"] = project_player_tabletop_turn(
        adjudication.get("tabletop_turn")
    )
    return projected


def with_player_safe_proposal_tabletop(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Replace every persisted tabletop payload in a player proposal copy."""

    projected = dict(proposal)
    projected["tabletop_turn"] = project_player_tabletop_turn(
        proposal.get("tabletop_turn")
    )
    actions = proposal.get("actions")
    if isinstance(actions, Sequence) and not isinstance(
        actions, (str, bytes, bytearray)
    ):
        safe_actions: list[object] = []
        for raw in actions:
            if not isinstance(raw, Mapping) or raw.get("action_type") != "tabletop_turn":
                safe_actions.append(raw)
                continue
            action = dict(raw)
            action["payload"] = project_player_tabletop_turn(raw.get("payload"))
            safe_actions.append(action)
        projected["actions"] = safe_actions
    return projected


__all__ = [
    "project_actor_execution_traces",
    "project_player_tabletop_turn",
    "with_player_safe_proposal_tabletop",
    "with_player_safe_tabletop",
]
