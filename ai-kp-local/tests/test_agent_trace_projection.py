from __future__ import annotations

import json

from ai_kp.application.agent_trace_projection import (
    project_actor_execution_traces,
    project_player_tabletop_turn,
)


def trace(**overrides):
    return {
        "schema_version": "entity-actor-trace.v1",
        "entity_id": "npc-1",
        "entity_title": "证人",
        "execution": "deterministic_fallback",
        "generation_attempt_count": 2,
        "error_codes": ["actor_output_rejected"],
        **overrides,
    }


def test_actor_trace_projection_is_a_strict_public_allowlist() -> None:
    result = project_actor_execution_traces(
        [{**trace(), "prompt": "SECRET_PROMPT", "private_boundary": "SECRET"}]
    )

    assert result == [trace()]
    assert "SECRET" not in json.dumps(result, ensure_ascii=False)


def test_actor_trace_projection_rejects_unknown_error_codes() -> None:
    assert project_actor_execution_traces(
        [trace(error_codes=["raw provider message: SECRET"])]
    ) == []


def test_player_tabletop_projection_strips_diagnostics_and_authority_binding() -> None:
    result = project_player_tabletop_turn(
        {
            "schema_version": "tabletop-turn.v1",
            "route": "roleplay",
            "attempt_count": 2,
            "audit_count": 1,
            "audit_reason": "SECRET_AUDIT_REASON",
            "validation_errors": ["SECRET_RAW_VALIDATION"],
            "frame": {
                "kind": "npc_dialogue",
                "goal": "询问",
                "method": "当面询问",
                "target_entity_ids": ["npc-1"],
                "dialogue": "你看到了什么？",
                "steps": [],
                "time_span": "",
                "ambiguity": None,
                "confidence": "high",
                "private_state_path": "SECRET_PATH",
            },
            "response": {
                "basis_hash": "a" * 64,
                "public_narration": "证人回答了问题。",
                "speaker_entity_ids": ["npc-1"],
                "source": "deterministic",
                "attempt_count": 2,
                "validation_errors": ["SECRET_PROVIDER_TEXT"],
                "actor_traces": [trace()],
                "private_boundaries": ["SECRET_BOUNDARY"],
            },
        }
    )

    assert result is not None
    encoded = json.dumps(result, ensure_ascii=False)
    assert "SECRET" not in encoded
    assert "basis_hash" not in encoded
    assert result["response"]["actor_traces"] == [trace()]
