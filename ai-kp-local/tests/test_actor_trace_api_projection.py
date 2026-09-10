from __future__ import annotations

import json

from ai_kp.api.routers.turns import get_action_adjudication, list_public_turns
from ai_kp.platform.sessions.models import AuthenticatedMember


def _tabletop_turn() -> dict:
    return {
        "schema_version": "tabletop-turn.v1",
        "route": "roleplay",
        "attempt_count": 2,
        "audit_count": 1,
        "audit_reason": "SECRET_AUDIT_REASON",
        "validation_errors": ["SECRET_RAW_ERROR"],
        "frame": {
            "kind": "npc_dialogue",
            "goal": "询问证人",
            "method": "当面交谈",
            "target_entity_ids": ["witness"],
            "dialogue": "你看到了什么？",
            "steps": [],
            "time_span": "",
            "ambiguity": None,
            "confidence": "high",
        },
        "response": {
            "basis_hash": "SECRET_BASIS_HASH",
            "public_narration": "证人明确回答了问题。",
            "speaker_entity_ids": ["witness"],
            "source": "deterministic",
            "attempt_count": 2,
            "validation_errors": ["SECRET_PROVIDER_TEXT"],
            "actor_traces": [{
                "schema_version": "entity-actor-trace.v1",
                "entity_id": "witness",
                "entity_title": "证人",
                "execution": "deterministic_fallback",
                "generation_attempt_count": 2,
                "error_codes": ["actor_output_rejected"],
                "private_boundary": "SECRET_BOUNDARY",
            }],
        },
    }


class _Repo:
    def get_player_action(self, action_id: str) -> dict:
        return {
            "id": action_id,
            "campaign_id": "campaign-1",
            "member_id": "member-1",
        }

    def get_active_parallel_action_batch_for_action(self, action_id: str):
        return None

    def get_action_adjudication(self, action_id: str) -> dict:
        return {
            "id": "adjudication-1",
            "action_id": action_id,
            "tabletop_turn": _tabletop_turn(),
        }

    def list_turn_proposals(self, campaign_id: str, status: str | None = None):
        assert campaign_id == "campaign-1" and status == "approved"
        return [{
            "id": "proposal-1",
            "player_action_id": "action-1",
            "player_action": "我询问证人。",
            "public_narration": "证人明确回答了问题。",
            "created_at": "2026-08-24 10:00:00",
            "decided_at": "2026-08-24 10:00:01",
            "tabletop_turn": _tabletop_turn(),
        }]


def _identity() -> AuthenticatedMember:
    return AuthenticatedMember(
        member_id="member-1",
        session_id="session-1",
        campaign_id="campaign-1",
        role="player",
        display_name="Player",
        pc_id="pc-1",
    )


def test_non_parallel_player_adjudication_uses_safe_tabletop_projection() -> None:
    result = get_action_adjudication("action-1", identity=_identity(), repo=_Repo())
    encoded = json.dumps(result, ensure_ascii=False)

    assert result["tabletop_turn"]["response"]["actor_traces"][0]["entity_id"] == "witness"
    assert "SECRET" not in encoded
    assert "basis_hash" not in encoded
    assert "validation_errors" not in encoded
    assert "audit_reason" not in encoded


def test_public_turns_include_only_safe_actor_traces() -> None:
    result = list_public_turns("campaign-1", identity=_identity(), repo=_Repo())
    encoded = json.dumps(result, ensure_ascii=False)

    assert result[0]["actor_traces"][0]["execution"] == "deterministic_fallback"
    assert "SECRET" not in encoded
    assert "tabletop_turn" not in result[0]
