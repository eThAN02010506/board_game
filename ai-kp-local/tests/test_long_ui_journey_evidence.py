from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from ai_kp.evaluation.long_ui_journey_evidence import (
    verify_long_ui_journey_evidence,
)


def _single_session_anchor() -> dict:
    session_id = "session-long-ui"
    started = datetime(2026, 8, 1, tzinfo=UTC)
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
    observe(
        "ui_scenario_reviewed",
        review_id="review-1",
        decision="approved",
        finding_ids=[],
    )
    observe("ui_scenario_published", contract_id="contract-1", contract_version="1")
    observe("ui_scenario_bound", contract_id="contract-1", contract_version="1")
    observe(
        "npc_response",
        action_id="action-npc",
        npc_entity_id="npc-1",
        response_text="The witness provides a concrete and verifiable dock record.",
        knowledge_fact_ids=["fact-1"],
    )
    observe(
        "direct_resolution",
        action_id="action-direct",
        result_text="The investigator takes the receipt and records its origin.",
        applied_effect_ids=["effect-receipt"],
    )
    observe(
        "skill_confirmed",
        action_id="action-success",
        check_id="check-success",
        skill_id="spot_hidden",
        difficulty="hard",
        confirmed_by_member_id="member-1",
    )
    observe(
        "check_success",
        action_id="action-success",
        check_id="check-success",
        degree="hard",
        result_text="The ledger reveals the matching date and vessel number.",
        applied_effect_ids=["effect-clue"],
    )
    observe(
        "skill_confirmed",
        action_id="action-failure",
        check_id="check-failure",
        skill_id="persuade",
        difficulty="regular",
        confirmed_by_member_id="member-2",
    )
    observe(
        "check_failure_cost",
        action_id="action-failure",
        check_id="check-failure",
        consequence_text="The archive closes for the day and access is revoked.",
        applied_effect_ids=["effect-closed"],
        fingerprint_before="b" * 64,
        fingerprint_after="c" * 64,
    )
    observe(
        "failure_followup",
        check_id="check-failure",
        mode="accept_failure",
        resolution_id="resolution-failure",
    )
    observe(
        "bounded_deviation",
        action_id="action-deviation",
        proposal_id="proposal-1",
        constraint_ids=["constraint-budget"],
        confirmed_by_member_id="member-3",
        result_text="A local contact checks one public record within the budget.",
        applied_effect_ids=["effect-lead"],
    )
    observe(
        "parallel_settlement",
        batch_id="batch-1",
        participant_member_ids=["member-1", "member-2", "member-3", "member-4"],
        action_ids=["parallel-1", "parallel-2", "parallel-3", "parallel-4"],
        resolution_ids=["result-1", "result-2", "result-3", "result-4"],
        regroup_location_id="location-1",
        authority_fingerprint="d" * 64,
    )
    observe(
        "authoritative_ending",
        ending_id="ending-1",
        authority_event_id="event-ending",
        authority_fingerprint="f" * 64,
    )
    return {
        "schema_version": 1,
        "journey_id": "single-session-anchor",
        "session_id": session_id,
        "automation_mode": "full_ai",
        "produced_at": (started + timedelta(hours=1)).isoformat(),
        "module": {"sha256": module_sha, "artifact_name": "module.docx"},
        "source_commit": "abcdef1234567",
        "model_generation": {
            "provider": "openai-compatible",
            "model": "real-model",
            "generation_id": "generation-1",
        },
        "ruleset": {"id": "coc7", "version": "7e"},
        "players": [
            {"member_id": f"member-{index}", "investigator_id": f"investigator-{index}"}
            for index in range(1, 5)
        ],
        "observations": observations,
    }


def _complete_evidence() -> dict:
    started = datetime(2026, 8, 1, 2, tzinfo=UTC)
    episodes: list[dict] = []
    for index in range(1, 11):
        snapshot_id = f"snapshot-{index}"
        episodes.append(
            {
                "sequence_no": index,
                "id": f"episode-{index}",
                "status": "ended",
                "version": 2,
                "event_start_rowid": 100 * index,
                "started_at": (started + timedelta(days=index - 1)).isoformat(),
                "ended_at": (started + timedelta(days=index - 1, hours=2)).isoformat(),
                "snapshot": {
                    "id": snapshot_id,
                    "client_end_id": f"client-end-{index}",
                    "event_window_hash": f"{index:064x}",
                    "event_ids": [f"event-{index}-1", f"event-{index}-2"],
                    "generation_cutoff": (
                        started + timedelta(days=index - 1, hours=2)
                    ).isoformat(),
                    "public_projection_hash": f"{index + 10:064x}",
                    "observer_projection_hash": f"{index + 20:064x}",
                    "kp_projection_hash": f"{index + 30:064x}",
                    "checkpoint_fingerprint": f"{index + 40:064x}",
                },
                "next_episode": (
                    {
                        "id": f"episode-{index + 1}",
                        "sequence_no": index + 1,
                        "client_continue_id": f"client-continue-{index}",
                    }
                    if index < 10
                    else None
                ),
            }
        )

    checkpoint = episodes[4]["snapshot"]
    ui_corroboration: list[dict] = []
    for index in range(1, 11):
        episode_started = started + timedelta(days=index - 1)
        episode_ended = episode_started + timedelta(hours=2)
        if index == 3:
            ui_corroboration.append(
                {
                    "kind": "authoritative_ending_visible",
                    "observed_at": (episode_started + timedelta(hours=1)).isoformat(),
                    "session_index": index,
                }
            )
        ui_corroboration.append(
            {
                "kind": "session_end_visible",
                "observed_at": (episode_ended + timedelta(minutes=1)).isoformat(),
                "session_index": index,
            }
        )
        if index < 10:
            ui_corroboration.append(
                {
                    "kind": "continue_campaign_visible",
                    "observed_at": (
                        episode_started + timedelta(days=1, minutes=1)
                    ).isoformat(),
                    "session_index": index,
                }
            )
    return {
        "schema_version": 1,
        "journey_id": "long-ui-journey-1",
        "campaign_id": "campaign-1",
        "table_session_id": "session-long-ui",
        "automation_mode": "full_ai",
        "produced_at": (started + timedelta(days=10)).isoformat(),
        "source_commit": "abcdef1234567",
        "ruleset": {"id": "coc7", "version": "7e"},
        "players": [
            {"member_id": f"member-{index}", "investigator_id": f"investigator-{index}"}
            for index in range(1, 5)
        ],
        "module_runs": [
            {
                "id": "run-1",
                "module_id": "module-1",
                "module_sha256": "a" * 64,
                "status": "active",
                "automation_mode": "full_ai",
                "started_at": started.isoformat(),
                "completed_at": None,
            }
        ],
        "episodes": episodes,
        "restart_checkpoint": {
            "before": {
                "observed_at": (started + timedelta(days=4, hours=3)).isoformat(),
                "process_instance_id": "process-old",
                "os_pid": 4101,
                "persistent_store_id": "store-long-ui",
                "checkpoint_snapshot_id": checkpoint["id"],
                "checkpoint_fingerprint": checkpoint["checkpoint_fingerprint"],
            },
            "stopped": {
                "observed_at": (started + timedelta(days=4, hours=3, minutes=1)).isoformat(),
                "process_instance_id": "process-old",
                "os_pid": 4101,
                "persistent_store_id": "store-long-ui",
                "clean_shutdown": True,
            },
            "started": {
                "observed_at": (started + timedelta(days=4, hours=3, minutes=2)).isoformat(),
                "process_instance_id": "process-new",
                "os_pid": 4102,
                "persistent_store_id": "store-long-ui",
                "health_status": 200,
            },
            "after": {
                "observed_at": (started + timedelta(days=5, minutes=1)).isoformat(),
                "process_instance_id": "process-new",
                "os_pid": 4102,
                "persistent_store_id": "store-long-ui",
                "checkpoint_snapshot_id": checkpoint["id"],
                "checkpoint_fingerprint": checkpoint["checkpoint_fingerprint"],
            },
        },
        "ui_corroboration": ui_corroboration,
        "single_session_anchor": _single_session_anchor(),
    }


def test_accepts_strict_ten_episode_ui_journey() -> None:
    result = verify_long_ui_journey_evidence(_complete_evidence())

    assert result.accepted is True
    assert result.blockers == ()
    assert result.metrics == {
        "journey_id": "long-ui-journey-1",
        "campaign_id": "campaign-1",
        "table_session_id": "session-long-ui",
        "players": 4,
        "module_runs": 1,
        "ended_episodes": 10,
        "longitudinal_accepted": True,
        "single_session_anchor_accepted": True,
        "restart_checkpoint_accepted": True,
    }


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("too_short", "at least 10"),
        ("not_ended", "status must be ended"),
        ("missing_prefix", "must start at 1"),
        ("sequence_gap", "sequence_no values must be consecutive"),
        ("duplicate_snapshot", "unique snapshot id"),
        ("broken_next", "does not identify the next consecutive episode"),
        ("terminal_next", "final episode next_episode must be null"),
    ],
)
def test_rejects_incomplete_or_forged_episode_chain(mutation: str, expected: str) -> None:
    evidence = deepcopy(_complete_evidence())
    episodes = evidence["episodes"]
    if mutation == "too_short":
        evidence["episodes"] = episodes[:9]
    elif mutation == "not_ended":
        episodes[4]["status"] = "in_progress"
    elif mutation == "missing_prefix":
        for episode in episodes:
            episode["sequence_no"] += 4
            if episode["next_episode"] is not None:
                episode["next_episode"]["sequence_no"] += 4
    elif mutation == "sequence_gap":
        episodes[4]["sequence_no"] = 8
    elif mutation == "duplicate_snapshot":
        episodes[4]["snapshot"]["id"] = episodes[3]["snapshot"]["id"]
    elif mutation == "broken_next":
        episodes[3]["next_episode"]["id"] = "episode-foreign"
    else:
        episodes[-1]["next_episode"] = {
            "id": "episode-11",
            "sequence_no": 11,
            "client_continue_id": "client-continue-10",
        }

    result = verify_long_ui_journey_evidence(evidence)

    assert result.accepted is False
    assert any(expected in blocker for blocker in result.blockers)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("same_instance", "distinct old/new"),
        ("same_pid", "distinct old/new"),
        ("wrong_stopped", "pre-restart identity"),
        ("wrong_started", "post-restart identity"),
        ("store_drift", "persistent store identity"),
        ("fingerprint_drift", "fingerprint changed"),
        ("foreign_checkpoint", "consecutive Continue"),
        ("terminal_checkpoint", "consecutive Continue"),
        ("continue_before_restart", "precedes the authoritative continued episode"),
        ("unclean", "clean_shutdown must be true"),
        ("unhealthy", "health_status must equal 200"),
        ("time_reversal", "strictly ordered"),
    ],
)
def test_rejects_uncorroborated_process_restart(mutation: str, expected: str) -> None:
    evidence = deepcopy(_complete_evidence())
    restart = evidence["restart_checkpoint"]
    if mutation == "same_instance":
        restart["started"]["process_instance_id"] = "process-old"
        restart["after"]["process_instance_id"] = "process-old"
    elif mutation == "same_pid":
        restart["started"]["os_pid"] = 4101
        restart["after"]["os_pid"] = 4101
    elif mutation == "wrong_stopped":
        restart["stopped"]["os_pid"] = 9999
    elif mutation == "wrong_started":
        restart["started"]["process_instance_id"] = "process-foreign"
    elif mutation == "store_drift":
        restart["after"]["persistent_store_id"] = "store-copy"
    elif mutation == "fingerprint_drift":
        restart["after"]["checkpoint_fingerprint"] = "9" * 64
    elif mutation == "foreign_checkpoint":
        restart["before"]["checkpoint_snapshot_id"] = "snapshot-foreign"
        restart["after"]["checkpoint_snapshot_id"] = "snapshot-foreign"
    elif mutation == "terminal_checkpoint":
        terminal = evidence["episodes"][-1]["snapshot"]
        for phase in ("before", "after"):
            restart[phase]["checkpoint_snapshot_id"] = terminal["id"]
            restart[phase]["checkpoint_fingerprint"] = terminal[
                "checkpoint_fingerprint"
            ]
    elif mutation == "continue_before_restart":
        restart["after"]["observed_at"] = (
            datetime(2026, 8, 5, 3, 3, tzinfo=UTC).isoformat()
        )
    elif mutation == "unclean":
        restart["stopped"]["clean_shutdown"] = False
    elif mutation == "unhealthy":
        restart["started"]["health_status"] = 503
    else:
        restart["started"]["observed_at"] = restart["stopped"]["observed_at"]

    result = verify_long_ui_journey_evidence(evidence)

    assert result.accepted is False
    assert result.metrics["restart_checkpoint_accepted"] is False
    assert any(expected in blocker for blocker in result.blockers)


def test_calls_gameplay_anchor_verifier_with_the_original_payload(monkeypatch) -> None:
    evidence = _complete_evidence()
    anchor = evidence["single_session_anchor"]
    received: list[object] = []

    class Result:
        accepted = True
        blockers: tuple[str, ...] = ()

    def spy(value):
        received.append(value)
        return Result()

    monkeypatch.setattr(
        "ai_kp.evaluation.long_ui_journey_evidence.verify_real_ui_journey_anchor",
        spy,
    )

    result = verify_long_ui_journey_evidence(evidence)

    assert result.accepted is True
    assert received == [anchor]
    assert received[0] is anchor


def test_rejects_when_strict_single_session_anchor_rejects() -> None:
    evidence = _complete_evidence()
    evidence["single_session_anchor"]["observations"] = []

    result = verify_long_ui_journey_evidence(evidence)

    assert result.accepted is False
    assert result.metrics["single_session_anchor_accepted"] is False
    assert any("single_session_anchor:" in blocker for blocker in result.blockers)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evidence: evidence.update({"long_ui_passed": True}),
        lambda evidence: evidence["episodes"][0].update({"episode_passed": True}),
        lambda evidence: evidence["module_runs"][0].update({"unknown": "field"}),
        lambda evidence: evidence.update({"api_key": "super-secret"}),
        lambda evidence: evidence["ruleset"].update(
            {"version": "Bearer abcdefghijklmnop"}
        ),
    ],
)
def test_rejects_unknown_pass_and_secret_content(mutate) -> None:
    evidence = _complete_evidence()
    mutate(evidence)

    result = verify_long_ui_journey_evidence(evidence)

    assert result.accepted is False
    assert any(
        fragment in blocker
        for blocker in result.blockers
        for fragment in ("unknown fields", "pass field", "secret")
    )


def test_rejects_non_full_ai_or_non_four_player_long_journey() -> None:
    evidence = _complete_evidence()
    evidence["automation_mode"] = "assisted"
    evidence["players"].pop()
    evidence["module_runs"] = []

    result = verify_long_ui_journey_evidence(evidence)

    assert result.accepted is False
    assert any("automation_mode must be full_ai" in blocker for blocker in result.blockers)
    assert any("exactly four" in blocker for blocker in result.blockers)
    assert any("module_runs must be a non-empty list" in blocker for blocker in result.blockers)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("session_end_visible", "Session End exactly once"),
        ("continue_campaign_visible", "Continue exactly once"),
        ("authoritative_ending_visible", "at least one authoritative ending"),
    ],
)
def test_rejects_missing_longitudinal_ui_corroboration(kind: str, expected: str) -> None:
    evidence = _complete_evidence()
    evidence["ui_corroboration"] = [
        item for item in evidence["ui_corroboration"] if item["kind"] != kind
    ]

    result = verify_long_ui_journey_evidence(evidence)

    assert result.accepted is False
    assert any(expected in blocker for blocker in result.blockers)
