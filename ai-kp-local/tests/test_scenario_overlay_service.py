from __future__ import annotations

from pathlib import Path

import pytest

from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.application.scenario_overlay_service import ScenarioOverlayService
from ai_kp.application.session_service import SessionService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.world_expansion_contract import (
    WorldExpansionContractProposal,
    WorldExpansionContractValidator,
)
from tests.scenario_contract_testkit import bind_payload_to_module
from tests.test_bounded_planning import delegation_payload
from tests.test_scenario_contract_service import create_module
from tests.test_world_expansion_contract import proposal_payload


def bound_run(repo: Repository, *, automation_level: str = "conservative") -> tuple[dict, dict]:
    campaign = repo.create_campaign("Overlay campaign")
    session = SessionService(repo).create(campaign["id"])
    kp = repo.authenticate_access_token(session["access_token"])
    assert kp is not None
    module = create_module(repo, campaign["id"])
    contracts = ScenarioContractService(repo)
    _, draft = contracts.compile_draft(
        module["id"],
        bind_payload_to_module(repo, module["id"], delegation_payload()),
        created_by_member_id=None,
    )
    assert draft is not None
    published = contracts.publish(
        draft["id"], expected_row_version=1, published_by_member_id=None
    )
    run = repo.start_campaign_module_run(
        campaign_id=campaign["id"],
        module_id=module["id"],
        current_scene_key=None,
        active_spoiler_tags=[],
        state={},
        started_by_member_id=kp.member_id,
    )
    if automation_level != "conservative":
        run = repo.set_module_run_automation_level(
            run["id"], expected_version=run["version"], level=automation_level,
            reason="overlay test", member_id=kp.member_id,
        )
    contracts.bind_run(run["id"], published["id"])
    state = repo.initialize_scenario_run_state(run["id"])
    return run, state


def expansion(repo: Repository, run: dict, state: dict) -> WorldExpansionContractProposal:
    binding = repo.get_module_run_contract_binding(run["id"])
    payload = proposal_payload(binding["contract"])
    payload["base_contract_hash"] = binding["contract_hash"]
    payload["base_state_version"] = state["state_version"]
    return WorldExpansionContractProposal.model_validate(payload)


def test_full_ai_atomically_activates_additive_overlay(tmp_path: Path) -> None:
    with db_session(tmp_path / "overlay.sqlite3") as connection:
        repo = Repository(connection)
        run, state = bound_run(repo, automation_level="ai_kp")
        base = repo.get_module_run_contract_binding(run["id"])["contract"]

        overlay = ScenarioOverlayService(repo).propose(
            run["id"],
            expansion(repo, run, state),
            created_by_member_id=None,
        )

        assert overlay["status"] == "active"
        effective = repo.get_module_run_contract_binding(run["id"])
        assert effective["active_overlay_id"] == overlay["id"]
        assert effective["contract"].source_version == base.source_version + 1
        assert len(effective["contract"].endings) == len(base.endings)
        assert any(
            item.operator_id == "expansion.side_route.request-records"
            for item in effective["contract"].operators
        )
        evolved = repo.get_scenario_run_state(run["id"])
        assert evolved["state_version"] == 1
        assert evolved["snapshot"].scenario_version == base.source_version + 1
        batches = repo.list_scenario_command_batches(run["id"])
        assert len(batches) == 1
        assert batches[0]["batch_kind"] == "world_expansion"
        assert batches[0]["commands"][0]["kind"] == "activate_contract_overlay"


def test_overlay_rejects_skill_keys_outside_the_ruleset_catalog(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "overlay-skill-authority.sqlite3") as connection:
        repo = Repository(connection)
        run, state = bound_run(repo, automation_level="ai_kp")
        payload = expansion(repo, run, state).model_dump(mode="json")
        payload["records"]["operators"][0]["skill_choices"][0]["skill_key"] = (
            "persuasiveness"
        )

        overlay = ScenarioOverlayService(repo).propose(
            run["id"],
            WorldExpansionContractProposal.model_validate(payload),
            created_by_member_id=None,
        )

        assert overlay["status"] == "rejected"
        assert "unknown_skill_key:persuasiveness" in overlay["decision"]["blockers"]


def test_balanced_overlay_requires_explicit_review(tmp_path: Path) -> None:
    with db_session(tmp_path / "overlay-review.sqlite3") as connection:
        repo = Repository(connection)
        run, state = bound_run(repo, automation_level="balanced")
        service = ScenarioOverlayService(repo)

        overlay = service.propose(
            run["id"],
            expansion(repo, run, state),
            created_by_member_id=None,
        )
        assert overlay["status"] == "review_required"
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 0

        activated = service.approve(overlay["id"], reviewed_by_member_id=None)
        assert activated["status"] == "active"
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 1


def test_overlay_rejects_stale_state_and_cannot_activate(tmp_path: Path) -> None:
    with db_session(tmp_path / "overlay-stale.sqlite3") as connection:
        repo = Repository(connection)
        run, state = bound_run(repo, automation_level="ai_kp")
        payload = expansion(repo, run, state).model_dump(mode="json")
        payload["base_state_version"] = 99

        overlay = ScenarioOverlayService(repo).propose(
            run["id"],
            WorldExpansionContractProposal.model_validate(payload),
            created_by_member_id=None,
        )

        assert overlay["status"] == "rejected"
        assert "base_state_version_mismatch" in overlay["decision"]["blockers"]
        with pytest.raises(ValueError, match="reviewable"):
            ScenarioOverlayService(repo).approve(
                overlay["id"], reviewed_by_member_id=None
            )


def test_overlay_proposal_key_cannot_be_reused_with_different_content(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "overlay-key.sqlite3") as connection:
        repo = Repository(connection)
        run, state = bound_run(repo)
        service = ScenarioOverlayService(repo)
        original = expansion(repo, run, state)
        service.propose(run["id"], original, created_by_member_id=None)
        changed = original.model_copy(update={"rationale": "Different content."})

        with pytest.raises(ValueError, match="reused"):
            service.propose(
                run["id"], changed, created_by_member_id=None,
            )


def test_reviewable_overlay_cannot_activate_after_run_is_paused(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "overlay-paused.sqlite3") as connection:
        repo = Repository(connection)
        run, state = bound_run(repo, automation_level="balanced")
        service = ScenarioOverlayService(repo)
        overlay = service.propose(
            run["id"], expansion(repo, run, state), created_by_member_id=None
        )
        repo.update_campaign_module_run(
            run["id"], {"expected_version": run["version"], "status": "paused"}
        )

        with pytest.raises(ValueError, match="active module run"):
            service.approve(overlay["id"], reviewed_by_member_id=None)

        assert repo.get_scenario_contract_overlay(overlay["id"])["status"] == (
            "review_required"
        )
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 0


def test_legacy_review_cannot_override_current_release_gates(tmp_path: Path) -> None:
    with db_session(tmp_path / "overlay-legacy-release-gate.sqlite3") as connection:
        repo = Repository(connection)
        run, state = bound_run(repo, automation_level="balanced")
        service = ScenarioOverlayService(repo)
        payload = expansion(repo, run, state).model_dump(mode="json")
        payload["records"]["operators"][0]["failure_commands"] = []
        proposal = WorldExpansionContractProposal.model_validate(payload)

        rejected = service.propose(
            run["id"], proposal, created_by_member_id=None
        )
        assert rejected["status"] == "rejected"
        base = repo.get_module_run_contract_binding(run["id"])["contract"]
        merged = WorldExpansionContractValidator._merge(base, proposal.records)
        merged_hash = ScenarioContractCompiler.contract_hash(merged)
        # Simulate a reviewable row persisted before branch consequences became
        # a hard release gate.
        connection.execute(
            """
            UPDATE scenario_contract_overlays
            SET status = 'review_required', merged_contract_hash = ?,
                merged_contract_json = ?
            WHERE id = ?
            """,
            (merged_hash, merged.model_dump_json(), rejected["id"]),
        )

        with pytest.raises(ValueError, match="no longer release-ready"):
            service.approve(rejected["id"], reviewed_by_member_id=None)

        assert repo.get_scenario_contract_overlay(rejected["id"])["status"] == (
            "review_required"
        )
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 0
