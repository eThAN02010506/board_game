from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from ai_kp.evaluation.ui_journey_evidence import (
    verify_real_ui_journey_anchor,
    verify_real_ui_journey_evidence,
)


def _complete_evidence() -> dict:
    session_id = "session-real-ui-001"
    started = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
    observations: list[dict] = []

    def observe(kind: str, **fields: object) -> None:
        sequence = len(observations) + 1
        observations.append(
            {
                "sequence": sequence,
                "observed_at": (started + timedelta(minutes=sequence)).isoformat(),
                "kind": kind,
                "session_id": session_id,
                **fields,
            }
        )

    module_sha = "a" * 64
    observe("ui_module_imported", document_id="doc-1", module_sha256=module_sha)
    observe("ui_scenario_reviewed", review_id="review-1", decision="approved", finding_ids=[])
    observe("ui_scenario_published", contract_id="contract-1", contract_version="7")
    observe("ui_scenario_bound", contract_id="contract-1", contract_version="7")
    observe(
        "npc_response",
        action_id="action-npc",
        npc_entity_id="npc-vittorio",
        response_text="他拍打观察窗并说出了一条可核验的码头记录。",
        knowledge_fact_ids=["fact-dock-ledger"],
    )
    observe(
        "direct_resolution",
        action_id="action-drink",
        result_text="调查员喝下咖啡并保留了空杯作为物证。",
        applied_effect_ids=["inventory-empty-cup"],
    )
    observe(
        "skill_confirmed",
        action_id="action-search",
        check_id="check-success",
        skill_id="spot_hidden",
        difficulty="hard",
        confirmed_by_member_id="member-1",
    )
    observe(
        "check_success",
        action_id="action-search",
        check_id="check-success",
        degree="hard",
        result_text="在破损的账页中找到了可对应日期与船号的货运记录。",
        applied_effect_ids=["clue-shipping-record"],
    )
    observe(
        "skill_confirmed",
        action_id="action-persuade",
        check_id="check-failure",
        skill_id="persuade",
        difficulty="regular",
        confirmed_by_member_id="member-2",
    )
    observe(
        "check_failure_cost",
        action_id="action-persuade",
        check_id="check-failure",
        consequence_text="报社保安将调查员请出大门，当日不再允许查档。",
        applied_effect_ids=["archive-access-closed-today"],
        fingerprint_before="b" * 64,
        fingerprint_after="c" * 64,
    )
    observe(
        "failure_followup",
        check_id="check-failure",
        mode="accept_failure",
        resolution_id="resolution-accept-cost",
    )
    observe(
        "bounded_deviation",
        action_id="action-hire",
        proposal_id="proposal-hire-local",
        constraint_ids=["budget-small-cash", "deadline-one-hour", "one-source-only"],
        confirmed_by_member_id="member-3",
        result_text="雇员只核实了一条公开码头记录，未代替调查员下结论。",
        applied_effect_ids=["lead-public-dock-record"],
    )
    observe(
        "parallel_settlement",
        batch_id="batch-1",
        participant_member_ids=["member-1", "member-2", "member-3", "member-4"],
        action_ids=["parallel-a1", "parallel-a2", "parallel-a3", "parallel-a4"],
        resolution_ids=["resolution-a1", "resolution-a2", "resolution-a3", "resolution-a4"],
        regroup_location_id="location-lighthouse-hall",
        authority_fingerprint="d" * 64,
    )
    observe(
        "continue_before_restart",
        continue_id="continue-checkpoint-1",
        authority_fingerprint="e" * 64,
        process_instance_id="backend-old",
        persistent_store_id="sqlite-campaign-store-1",
    )
    observe(
        "backend_stopped",
        process_instance_id="backend-old",
        os_pid=4101,
        exit_code=0,
        persistent_store_id="sqlite-campaign-store-1",
    )
    observe(
        "backend_started",
        process_instance_id="backend-new",
        os_pid=4102,
        health_status=200,
        persistent_store_id="sqlite-campaign-store-1",
    )
    observe(
        "continue_after_restart",
        continue_id="continue-checkpoint-1",
        authority_fingerprint="e" * 64,
        process_instance_id="backend-new",
        persistent_store_id="sqlite-campaign-store-1",
    )
    observe(
        "authoritative_ending",
        ending_id="ending-escape",
        authority_event_id="event-ending-committed",
        authority_fingerprint="f" * 64,
    )
    return {
        "schema_version": 1,
        "journey_id": "journey-001",
        "session_id": session_id,
        "automation_mode": "full_ai",
        "produced_at": (started + timedelta(hours=2)).isoformat(),
        "module": {"sha256": module_sha, "artifact_name": "authorized-module.docx"},
        "source_commit": "abcdef1234567",
        "model_generation": {
            "provider": "openai-compatible",
            "model": "small-reasoning-model",
            "generation_id": "configured-generation-4",
        },
        "ruleset": {"id": "coc7", "version": "7e"},
        "players": [
            {"member_id": f"member-{index}", "investigator_id": f"investigator-{index}"}
            for index in range(1, 5)
        ],
        "observations": observations,
    }


def test_real_ui_journey_accepts_complete_observation_chain() -> None:
    result = verify_real_ui_journey_evidence(_complete_evidence())

    assert result.accepted is True
    assert result.blockers == ()
    assert result.metrics["players"] == 4
    assert result.metrics["observations"] == 18


def test_gameplay_anchor_accepts_same_authority_without_legacy_restart() -> None:
    evidence = _complete_evidence()
    evidence["observations"] = [
        observation
        for observation in evidence["observations"]
        if observation["kind"]
        not in {
            "backend_stopped",
            "backend_started",
            "continue_before_restart",
            "continue_after_restart",
        }
    ]

    result = verify_real_ui_journey_anchor(evidence)

    assert result.accepted is True
    assert result.blockers == ()


def test_gameplay_anchor_rejects_restart_observations_owned_by_long_gate() -> None:
    result = verify_real_ui_journey_anchor(_complete_evidence())

    assert result.accepted is False
    assert any(
        "gameplay anchor must not contain restart observations" in blocker
        for blocker in result.blockers
    )


def test_real_ui_journey_rejects_producer_reported_pass_boolean() -> None:
    evidence = _complete_evidence()
    evidence["ui_journey_passed"] = True

    result = verify_real_ui_journey_evidence(evidence)

    assert result.accepted is False
    assert any("unknown fields: ui_journey_passed" in blocker for blocker in result.blockers)


def test_real_ui_journey_rejects_producer_classified_ending_type() -> None:
    evidence = _complete_evidence()
    ending = next(
        item for item in evidence["observations"] if item["kind"] == "authoritative_ending"
    )
    ending["ending_type"] = "success"

    result = verify_real_ui_journey_evidence(evidence)

    assert result.accepted is False
    assert any("unknown fields: ending_type" in blocker for blocker in result.blockers)


@pytest.mark.parametrize(
    "missing_kind",
    [
        "npc_response",
        "direct_resolution",
        "skill_confirmed",
        "check_success",
        "check_failure_cost",
        "failure_followup",
        "bounded_deviation",
        "parallel_settlement",
        "authoritative_ending",
    ],
)
def test_real_ui_journey_rejects_missing_gameplay_branch(missing_kind: str) -> None:
    evidence = _complete_evidence()
    evidence["observations"] = [
        observation
        for observation in evidence["observations"]
        if observation["kind"] != missing_kind
    ]

    result = verify_real_ui_journey_evidence(evidence)

    assert result.accepted is False
    assert any(missing_kind in blocker for blocker in result.blockers)


@pytest.mark.parametrize("mutation", ["same_process", "fingerprint_drift"])
def test_real_ui_journey_rejects_fake_restart_or_fingerprint_drift(mutation: str) -> None:
    evidence = deepcopy(_complete_evidence())
    observations = {item["kind"]: item for item in evidence["observations"]}
    if mutation == "same_process":
        observations["backend_started"]["process_instance_id"] = "backend-old"
        observations["backend_started"]["os_pid"] = 4101
        observations["continue_after_restart"]["process_instance_id"] = "backend-old"
    else:
        observations["continue_after_restart"]["authority_fingerprint"] = "9" * 64

    result = verify_real_ui_journey_evidence(evidence)

    assert result.accepted is False
    assert any(
        fragment in blocker
        for blocker in result.blockers
        for fragment in ("distinct process", "fingerprint drifted")
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("field", "super-secret"),
        ("value", "sk-not-allowed-in-evidence"),
    ],
)
def test_real_ui_journey_rejects_secret_fields_and_values(path: str, value: str) -> None:
    evidence = _complete_evidence()
    if path == "field":
        evidence["api_key"] = value
    else:
        evidence["model_generation"]["generation_id"] = value

    result = verify_real_ui_journey_evidence(evidence)

    assert result.accepted is False
    assert any("secret" in blocker for blocker in result.blockers)
