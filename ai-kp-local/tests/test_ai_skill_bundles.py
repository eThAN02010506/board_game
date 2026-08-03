from importlib.resources import files
from typing import cast

import pytest

from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.director.context_builder import ContextBuilder
from ai_kp.director.session_recap import build_session_recap_context
from ai_kp.director.skills import (
    AiSkillWorkflow,
    compose_ai_skill_instructions,
    list_ai_skills,
    resolve_ai_skill_composition,
    resolve_ai_skills,
)
from ai_kp.director.skills.bundles import load_ai_skill_bundle

RUNTIME_BUNDLES = (
    "understand-module-scene",
    "understand-player-action",
    "portray-npc",
    "direct-scene",
    "narrate-check-consequence",
    "expand-world",
    "curate-session-memory",
    "review-output-safety",
)


@pytest.mark.parametrize("bundle_name", RUNTIME_BUNDLES)
def test_runtime_ai_skill_bundle_is_complete_and_has_valid_ui_metadata(
    bundle_name: str,
) -> None:
    bundle = load_ai_skill_bundle(bundle_name)
    metadata = (
        files("ai_kp.director.skills")
        .joinpath("bundles", bundle_name, "agents", "openai.yaml")
        .read_text(encoding="utf-8")
    )

    assert bundle.name == bundle_name
    assert bundle.description
    assert len(bundle.content_hash) == 64
    assert "TODO" not in bundle.instructions
    assert "candidate" in bundle.instructions.lower() or "proposal" in bundle.instructions.lower()
    assert f"${bundle_name}" in metadata


def test_skill_composition_is_allow_listed_hashed_and_deduplicated() -> None:
    player_skills = resolve_ai_skills(
        (
            "platform.turn_proposal",
            "platform.npc_portrayal",
            "platform.scene_direction",
            "platform.output_safety_review",
        )
    )
    instructions = compose_ai_skill_instructions(player_skills)

    assert instructions.count("[AI Skill:") == 4
    assert "platform.turn_proposal@1.0.0" in instructions
    assert "platform.output_safety_review@1.0.0" in instructions
    assert instructions.count("sha256=") == 4
    assert "guaranteed safe" in instructions

    consequence = compose_ai_skill_instructions(
        resolve_ai_skills(("platform.check_consequence_narration",))
    )
    assert "# Narrate Check Consequence" in consequence
    assert "# Direct Scene" not in consequence


def test_session_recap_context_includes_the_memory_curation_bundle() -> None:
    skills = resolve_ai_skills(("platform.session_recap",))
    context = build_session_recap_context(
        {
            "campaign_id": "campaign-1",
            "event_window_hash": "window-hash",
            "session": {"title": "Frozen session"},
            "events": [],
        },
        skill_instructions=compose_ai_skill_instructions(skills),
    )

    system_prompt = context.messages[0]["content"]
    assert "[AI Skill: platform.session_recap@1.0.0" in system_prompt
    assert "# Curate Session Memory" in system_prompt
    assert context.token_estimate >= len(system_prompt) // 4


def test_skill_resolution_rejects_duplicates_unknown_ids_and_unsafe_paths() -> None:
    with pytest.raises(ValueError, match="unique skill IDs"):
        resolve_ai_skills(("platform.turn_proposal", "platform.turn_proposal"))
    with pytest.raises(ValueError, match="not installed"):
        resolve_ai_skills(("model-generated.unsafe",))
    with pytest.raises(ValueError, match="Invalid AI skill bundle name"):
        load_ai_skill_bundle("../outside")
    with pytest.raises(ValueError, match="not installed"):
        load_ai_skill_bundle("missing-skill")


def test_workflow_compositions_are_centralized_and_keep_primary_skill_compatibility() -> None:
    expected_primary: dict[AiSkillWorkflow, str] = {
        "player_action": "platform.turn_proposal",
        "check_consequence": "platform.check_consequence_narration",
        "world_expansion": "platform.world_expansion",
        "session_recap": "platform.session_recap",
    }

    for workflow, primary_skill_id in expected_primary.items():
        composition = resolve_ai_skill_composition(workflow)
        assert composition[0].skill_id == primary_skill_id
        assert len(composition) == len({skill.skill_id for skill in composition})

    with pytest.raises(ValueError, match="Unknown AI skill workflow"):
        resolve_ai_skill_composition(cast(AiSkillWorkflow, "not-a-workflow"))


def test_skill_catalogue_exposes_bundle_hashes_without_granting_authority() -> None:
    catalogue = list_ai_skills()
    bundled = [item for item in catalogue if item["bundle_name"] is not None]

    assert len(bundled) == 8
    assert all(len(item["bundle_hash"]) == 64 for item in bundled)
    assert all(item["authority"] == "proposal_only" for item in catalogue)
    assert all("state.write" not in item["allowed_tools"] for item in catalogue)


def test_skill_instructions_are_accounted_for_by_the_context_budget(tmp_path) -> None:
    with db_session(tmp_path / "skill-budget.sqlite3") as connection:
        campaign = Repository(connection).create_campaign("Skill budget")

        with pytest.raises(ValueError, match="fixed prompt exceed"):
            ContextBuilder(connection, max_context_tokens=300).build(
                campaign_id=campaign["id"],
                player_action="I look around.",
                skill_instructions="bounded skill instruction " * 200,
            )
