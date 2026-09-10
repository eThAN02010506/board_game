from ai_kp.evaluation.long_campaign_evidence import (
    REQUIRED_JOURNEY_COVERAGE,
    REQUIRED_PERSONAS,
    REQUIRED_RPS,
    SCHEMA_VERSION,
    evaluate_long_campaign_evidence,
)


def _persona_evidence(persona_id: str) -> dict:
    return {
        "persona_id": persona_id,
        "actor_mode": (
            "gm_ui"
            if persona_id in {"new_gm", "veteran_gm", "ai_assisted_gm"}
            else "player_ui"
        ),
        "source_commit": "abcdef1234567",
        "observed_at": "2026-09-01T01:00:00+00:00",
        "artifact_ref": f"evidence/persona-{persona_id}.json",
        "artifact_sha256": "a" * 64,
        "observation_ids": [f"persona-observation-{persona_id}"],
        "interaction_count": 3,
    }


def _rps_evidence(requirement_id: str) -> dict:
    return {
        "requirement_id": requirement_id,
        "surface": "gm_ui" if requirement_id == "RPS-06" else "player_ui",
        "source_commit": "abcdef1234567",
        "observed_at": "2026-09-01T01:00:00+00:00",
        "artifact_ref": f"evidence/{requirement_id.lower()}.json",
        "artifact_sha256": "b" * 64,
        "observation_ids": [f"observation-{requirement_id}"],
    }


def _coverage_evidence(coverage_id: str) -> dict:
    surface = {
        "combat": "kp_player_ui",
        "inventory_loot": "kp_player_ui",
        "death": "kp_player_ui",
        "replacement": "kp_player_ui",
        "late_join": "multi_ui",
        "player_leave": "multi_ui",
    }.get(coverage_id, "player_ui")
    return {
        "coverage_id": coverage_id,
        "surface": surface,
        "source_commit": "abcdef1234567",
        "observed_at": "2026-09-01T01:00:00+00:00",
        "artifact_ref": f"evidence/coverage-{coverage_id}.json",
        "artifact_sha256": "c" * 64,
        "observation_ids": [f"coverage-observation-{coverage_id}"],
    }


def _complete_evidence() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_commit": "abcdef1234567",
        "scripted_sessions": 10,
        "durability_sessions": 20,
        "equivalent_table_minutes": 3000,
        "passed_edge_case_ids": [f"EDGE-{index:03d}" for index in range(1, 51)],
        "persona_evidence": [
            _persona_evidence(persona_id) for persona_id in sorted(REQUIRED_PERSONAS)
        ],
        "rps_evidence": [
            _rps_evidence(requirement_id) for requirement_id in sorted(REQUIRED_RPS)
        ],
        "journey_coverage_evidence": [
            _coverage_evidence(coverage_id)
            for coverage_id in sorted(REQUIRED_JOURNEY_COVERAGE)
        ],
        "repair_rounds": [
            {
                "id": f"round-{index}",
                "input_version": f"commit-{index}",
                "evidence_ref": f"evidence/round-{index}.json",
                "full_retest_passed": True,
                "remaining_risks": [],
            }
            for index in range(1, 4)
        ],
        "ui_journey_passed": True,
        "four_player_full_ai_passed": True,
        "restart_recovery_passed": True,
        "model_switch_passed": True,
        "index_rebuild_passed": True,
        "model_off_replay_passed": True,
        "authority_fingerprint_stable": True,
        "secret_leak_count_is_zero": True,
        "lifecycle_durability_passed": True,
        "open_defects": {"P0": 0, "P1": 0, "P2": 3},
    }


def test_long_campaign_gate_accepts_only_complete_independent_evidence() -> None:
    result = evaluate_long_campaign_evidence(_complete_evidence())
    assert result.ready is True
    assert result.blockers == ()
    assert result.metrics["unique_edge_cases"] == 50


def test_long_campaign_gate_fails_closed_for_missing_or_duplicated_evidence() -> None:
    evidence = _complete_evidence()
    evidence["passed_edge_case_ids"] = ["EDGE-001"] * 50
    evidence["repair_rounds"][2]["full_retest_passed"] = False
    evidence["persona_evidence"] = [
        item
        for item in evidence["persona_evidence"]
        if item["persona_id"] != "chaos_sandbox_player"
    ]
    evidence["rps_evidence"] = [
        item
        for item in evidence["rps_evidence"]
        if item["requirement_id"] != "RPS-10"
    ]
    evidence["ui_journey_passed"] = "yes"
    evidence["lifecycle_durability_passed"] = False
    evidence["open_defects"] = {"P0": 0, "P1": 1}

    result = evaluate_long_campaign_evidence(evidence)

    assert result.ready is False
    assert result.metrics["unique_edge_cases"] == 1
    assert result.metrics["repair_rounds"] == 2
    assert any("chaos_sandbox_player" in item for item in result.blockers)
    assert any("RPS-10" in item for item in result.blockers)
    assert any("player UI" in item for item in result.blockers)
    assert any("character lifecycle" in item for item in result.blockers)
    assert any("P1" in item for item in result.blockers)


def test_long_campaign_gate_rejects_unscoped_persona_and_rps_claims() -> None:
    evidence = _complete_evidence()
    evidence["persona_evidence"][0]["actor_mode"] = "player_ui"
    evidence["rps_evidence"][0]["artifact_sha256"] = "not-a-digest"
    evidence["validated_personas"] = sorted(REQUIRED_PERSONAS)
    evidence["validated_rps"] = sorted(REQUIRED_RPS)

    result = evaluate_long_campaign_evidence(evidence)

    assert result.ready is False
    assert result.metrics["validated_personas"] == 7
    assert result.metrics["validated_rps"] == 11


def test_long_campaign_gate_binds_experience_evidence_to_current_commit() -> None:
    evidence = _complete_evidence()
    evidence["persona_evidence"][0]["source_commit"] = "1234567"
    evidence["rps_evidence"][0]["observation_ids"] = ["same", "same"]

    result = evaluate_long_campaign_evidence(evidence)

    assert result.ready is False
    assert result.metrics["validated_personas"] == 7
    assert result.metrics["validated_rps"] == 11


def test_long_campaign_gate_requires_scoped_scripted_journey_coverage() -> None:
    evidence = _complete_evidence()
    evidence["journey_coverage_evidence"] = [
        item
        for item in evidence["journey_coverage_evidence"]
        if item["coverage_id"] != "growth"
    ]
    next(
        item
        for item in evidence["journey_coverage_evidence"]
        if item["coverage_id"] == "exploration"
    )["surface"] = "kp_player_ui"

    result = evaluate_long_campaign_evidence(evidence)

    assert result.ready is False
    assert result.metrics["validated_journey_coverage"] == 12
    assert any("growth" in blocker for blocker in result.blockers)


def test_long_campaign_gate_does_not_coerce_counts_or_defect_status() -> None:
    evidence = _complete_evidence()
    evidence["durability_sessions"] = "20"
    evidence["equivalent_table_minutes"] = True
    evidence.pop("open_defects")

    result = evaluate_long_campaign_evidence(evidence)

    assert result.ready is False
    assert result.metrics["durability_sessions"] == 0
    assert result.metrics["equivalent_table_minutes"] == 0
    assert result.metrics["open_p0"] == -1
