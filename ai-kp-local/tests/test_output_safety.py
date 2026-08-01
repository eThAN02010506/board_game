import json

from ai_kp.director.output_safety import remove_precommitted_world_effects


def _candidate() -> dict:
    return {
        "public_narration": "You prepare to act.",
        "kp_notes": "",
        "action_ruling": {
            "goal": "open the door",
            "method": "force it",
            "target": "door",
            "feasibility": "possible",
            "resolution": "check",
            "reason": "the door is stuck",
            "maximum_effect": "open the door",
            "alternative": "find a key",
        },
        "proposed_checks": [
            {"skill": "STR", "difficulty": "regular", "reason": "force the door"}
        ],
        "proposed_events": [{"event_type": "door_opened", "summary": "Door opened."}],
        "proposed_memories": [{"text": "Door opened."}],
        "proposed_npc_updates": [{"npc_id": "npc_unknown"}],
        "proposed_map_moves": [{"token_id": "token_unknown"}],
        "proposed_facts": [{"fact_type": "canonical_fact"}],
    }


def test_reductive_safety_repair_removes_only_precommitted_world_effects() -> None:
    payload = _candidate()
    repaired = json.loads(
        remove_precommitted_world_effects(json.dumps(payload, ensure_ascii=False))
    )

    assert repaired["action_ruling"] == payload["action_ruling"]
    assert repaired["proposed_checks"] == payload["proposed_checks"]
    assert repaired["public_narration"] == payload["public_narration"]
    assert repaired["proposed_events"] == []
    assert repaired["proposed_memories"] == []
    assert repaired["proposed_npc_updates"] == []
    assert repaired["proposed_map_moves"] == []
    assert repaired["proposed_facts"] == []


def test_reductive_safety_repair_does_not_rewrite_unrelated_or_malformed_output() -> None:
    automatic = _candidate()
    automatic["action_ruling"]["resolution"] = "automatic"
    raw_automatic = json.dumps(automatic)

    assert remove_precommitted_world_effects(raw_automatic) == raw_automatic
    assert remove_precommitted_world_effects("not json") == "not json"
    assert remove_precommitted_world_effects('{"proposed_checks": []}') == (
        '{"proposed_checks": []}'
    )
