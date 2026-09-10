import json

import pytest

from ai_kp.application.campaign_service import CampaignService, CreateCampaignCommand
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.director.skills import get_ai_skill, list_ai_skills
from ai_kp.platform.randomness import (
    DiceComponent,
    DiceRollRequest,
    DiceRollResult,
    SecureDiceRoller,
)
from ai_kp.rule_authoring.models import RuleObject
from ai_kp.rulesets import get_campaign_ruleset, get_ruleset, list_rulesets
from ai_kp.rulesets.base import RulesetManifest


def test_ruleset_manifest_exposes_versioned_capabilities_and_support_level() -> None:
    manifest = list_rulesets()[0]

    assert manifest["contract_version"] == "1.0.0"
    assert manifest["support_level"] == "playable_alpha"
    assert manifest["character_schema_version"] == "coc7-investigator-v1"
    assert manifest["event_schema_version"] == "coc7-event-v1"
    assert manifest["knowledge_namespace"] == "rulesets/coc7/2002c"
    assert manifest["license"]["id"] == "user-supplied-proprietary-reference"
    assert "skill_check" in manifest["capabilities"]
    assert "freeform" in manifest["map_modes"]


def test_knowledge_only_manifest_does_not_require_aliases_maps_or_ui() -> None:
    manifest = RulesetManifest(
        contract_version="1.0.0",
        ruleset_id="example-knowledge",
        version="2026.1",
        slug="example",
        display_name="Example Knowledge",
        engine_family="unknown",
        source_version="user supplied",
        aliases=(),
        character_schema_version="unavailable",
        event_schema_version="unavailable",
        knowledge_namespace="rulesets/example/2026.1",
        supported_locales=("en",),
        support_level="knowledge_only",
        source_reference={"kind": "user_upload"},
        license={"id": "unknown", "content_scope": "local user-supplied source"},
        capabilities=("source_retrieval",),
        map_modes=(),
        ui_slots=(),
    )

    assert manifest.as_dict()["aliases"] == []
    with pytest.raises(ValueError, match="valid SemVer"):
        RulesetManifest(
            **{
                **manifest.__dict__,
                "contract_version": "v1",
            }
        )


def test_campaign_pins_exact_ruleset_contract_and_rejects_version_drift(tmp_path) -> None:
    with db_session(tmp_path / "ruleset-pin.sqlite3") as connection:
        campaign = CampaignService(Repository(connection)).create(
            CreateCampaignCommand("Pinned CoC7")
        )

        assert campaign["ruleset_id"] == "coc7-keeper-cn-2002c"
        assert campaign["ruleset_version"] == "2002c"
        assert campaign["character_schema_version"] == "coc7-investigator-v1"
        assert campaign["event_schema_version"] == "coc7-event-v1"
        assert get_campaign_ruleset(campaign) is get_ruleset("coc7")

        changed = {**campaign, "ruleset_version": "future-edition"}
        with pytest.raises(ValueError, match="version is not installed"):
            get_campaign_ruleset(changed)


def test_generic_dice_evidence_is_replayable_and_tamper_evident() -> None:
    request = DiceRollRequest(
        components=(
            DiceComponent("d20", count=2, sides=20),
            DiceComponent("stress", count=3, sides=6),
        )
    )
    result = SecureDiceRoller().roll(request)
    restored = DiceRollResult.from_dict(result.as_dict())

    assert restored.as_dict() == result.as_dict()
    assert len(result.evidence_fingerprint) == 64
    assert all(1 <= value <= 20 for value in result.rolls["d20"])
    assert all(1 <= value <= 6 for value in result.rolls["stress"])

    tampered = json.loads(json.dumps(result.as_dict()))
    tampered["rolls"]["d20"][0] = 1 if result.rolls["d20"][0] != 1 else 2
    with pytest.raises(ValueError, match="fingerprint does not match"):
        DiceRollResult.from_dict(tampered)
    unsigned = {**result.as_dict()}
    unsigned.pop("evidence_fingerprint")
    with pytest.raises(ValueError, match="fingerprint is required"):
        DiceRollResult.from_dict(unsigned)
    with pytest.raises(TypeError, match="values must be integers"):
        DiceRollResult.create(request, {"d20": [True, 2], "stress": [1, 2, 3]})


def test_ai_skills_are_explicit_proposal_only_contracts() -> None:
    skills = list_ai_skills()

    assert {item["skill_id"] for item in skills} == {
        "platform.module_scene_understanding",
        "platform.turn_proposal",
        "platform.npc_portrayal",
        "platform.scene_direction",
        "platform.output_safety_review",
        "platform.check_consequence_narration",
        "platform.world_expansion",
        "platform.session_recap",
        "platform.enemy_turn_selection",
    }
    assert all(item["authority"] == "proposal_only" for item in skills)
    assert all(item["source_requirements"] for item in skills)
    assert get_ai_skill("platform.world_expansion").category == "world_expansion"
    with pytest.raises(ValueError, match="not installed"):
        get_ai_skill("model-generated.unsafe")


def test_rulebook_can_extract_source_bound_skill_guidance_without_executing_it() -> None:
    payload = {
        "rule_key": "coc7.keeper.foreshadowing",
        "ruleset_id": "coc7-keeper-cn-2002c",
        "title": "伏笔主持建议",
        "rule_type": "skill_guidance",
        "summary": "主持人应通过可观察细节逐步提供危险征兆。",
        "audience": "kp",
        "tags": ["narration", "keeper"],
        "execution": {
            "kind": "reference_only",
            "inputs": [],
            "branches": [],
            "rows": [],
        },
        "citations": [
            {
                "chunk_id": "rulechunk_1",
                "page": 42,
                "evidence_text": "通过逐渐明显的征兆营造危险。",
            }
        ],
        "confidence": 0.8,
    }
    candidate = RuleObject.model_validate(payload)

    assert candidate.rule_type == "skill_guidance"
    assert candidate.execution.kind == "reference_only"
    assert candidate.citations[0].chunk_id == "rulechunk_1"
    with pytest.raises(ValueError, match="skill_guidance must be reference_only"):
        RuleObject.model_validate(
            {
                **payload,
                "execution": {
                    "kind": "lookup_table",
                    "inputs": ["mood"],
                    "lookup_input": "mood",
                    "rows": [{"equals": "fear", "output": {"tone": "ominous"}}],
                    "branches": [],
                },
            }
        )
