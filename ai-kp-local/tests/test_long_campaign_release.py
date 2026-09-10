import hashlib
from pathlib import Path

import pytest

from ai_kp.evaluation.long_campaign_evidence import (
    REQUIRED_JOURNEY_COVERAGE,
    REQUIRED_PERSONAS,
    REQUIRED_RPS,
    SCHEMA_VERSION,
)
from ai_kp.evaluation.long_campaign_release import (
    LongCampaignReleaseError,
    assemble_long_campaign_release,
)

SOURCE_COMMIT = "abcdef1234567"


def test_assembles_only_complete_hash_verified_release(tmp_path: Path) -> None:
    foundation, journey, repairs = _inputs(tmp_path)

    result = assemble_long_campaign_release(foundation, journey, repairs)

    assert result["gate"]["ready"] is True
    assert result["scripted_sessions"] == 10
    assert result["four_player_full_ai_passed"] is True
    assert result["gate"]["metrics"]["validated_rps"] == 12


def test_rejects_changed_or_missing_artifact(tmp_path: Path) -> None:
    foundation, journey, repairs = _inputs(tmp_path)
    artifact = Path(journey["rps_evidence_candidates"][0]["artifact_ref"])
    artifact.write_text("changed after verification", encoding="utf-8")

    with pytest.raises(LongCampaignReleaseError, match="digest mismatch"):
        assemble_long_campaign_release(foundation, journey, repairs)


def test_rejects_cross_commit_or_duplicate_claims(tmp_path: Path) -> None:
    foundation, journey, repairs = _inputs(tmp_path)
    journey["strict_evidence"]["source_commit"] = "1234567"
    with pytest.raises(LongCampaignReleaseError, match="same source commit"):
        assemble_long_campaign_release(foundation, journey, repairs)

    journey["strict_evidence"]["source_commit"] = SOURCE_COMMIT
    journey["rps_evidence_candidates"].append(
        dict(journey["rps_evidence_candidates"][0])
    )
    with pytest.raises(LongCampaignReleaseError, match="duplicate merged"):
        assemble_long_campaign_release(foundation, journey, repairs)


def _inputs(tmp_path: Path) -> tuple[dict, dict, dict]:
    journey_artifact = tmp_path / "journey.json"
    journey_artifact.write_text('{"kind":"journey"}\n', encoding="utf-8")
    journey_digest = _digest(journey_artifact)
    repair_artifacts = []
    for index in range(1, 4):
        path = tmp_path / f"repair-{index}.json"
        path.write_text(f'{{"round":{index}}}\n', encoding="utf-8")
        repair_artifacts.append(path)

    def provenance() -> dict:
        return {
            "source_commit": SOURCE_COMMIT,
            "observed_at": "2026-09-01T01:00:00+00:00",
            "artifact_ref": str(journey_artifact),
            "artifact_sha256": journey_digest,
        }

    player_personas = sorted(
        REQUIRED_PERSONAS - {"new_gm", "veteran_gm", "ai_assisted_gm"}
    )
    gm_personas = sorted(REQUIRED_PERSONAS - set(player_personas))
    journey = {
        "accepted": True,
        "strict_evidence": {"source_commit": SOURCE_COMMIT},
        "verification": {
            "accepted": True,
            "metrics": {
                "players": 4,
                "ended_episodes": 10,
                "restart_checkpoint_accepted": True,
                "journey_id": "journey-1",
                "campaign_id": "campaign-1",
            },
        },
        "persona_evidence_candidates": [
            {
                "persona_id": persona,
                "actor_mode": "player_ui",
                **provenance(),
                "observation_ids": [f"ui:{persona}"],
                "interaction_count": 2,
            }
            for persona in player_personas
        ],
        "gm_persona_evidence_candidates": [
            {
                "persona_id": persona,
                "actor_mode": "gm_ui",
                **provenance(),
                "observation_ids": [f"ui:{persona}"],
                "interaction_count": 2,
            }
            for persona in gm_personas
        ],
        "rps_evidence_candidates": [
            {
                "requirement_id": requirement,
                "surface": "gm_ui" if requirement == "RPS-06" else "player_ui",
                **provenance(),
                "observation_ids": [f"ui:{requirement}"],
            }
            for requirement in sorted(REQUIRED_RPS)
        ],
        "journey_coverage_evidence_candidates": [
            {
                "coverage_id": coverage,
                "surface": _coverage_surface(coverage),
                **provenance(),
                "observation_ids": [f"ui:{coverage}"],
            }
            for coverage in sorted(REQUIRED_JOURNEY_COVERAGE)
        ],
    }
    foundation = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": SOURCE_COMMIT,
        "scripted_sessions": 20,
        "durability_sessions": 20,
        "equivalent_table_minutes": 3000,
        "passed_edge_case_ids": [f"EDGE-{index:03d}" for index in range(1, 51)],
        "restart_recovery_passed": True,
        "model_switch_passed": True,
        "index_rebuild_passed": True,
        "model_off_replay_passed": True,
        "authority_fingerprint_stable": True,
        "secret_leak_count_is_zero": True,
        "lifecycle_durability_passed": True,
    }
    repairs = {
        "source_commit": SOURCE_COMMIT,
        "report_sha256": "a" * 64,
        "rounds": [
            {
                "id": f"round-{index}",
                "input_version": f"commit-{index}",
                "evidence_ref": str(path),
                "evidence_sha256": _digest(path),
                "full_retest_passed": True,
                "remaining_risks": [],
            }
            for index, path in enumerate(repair_artifacts, start=1)
        ],
        "open_defects": {"P0": 0, "P1": 0},
    }
    return foundation, journey, repairs


def _coverage_surface(coverage: str) -> str:
    return {
        "combat": "kp_player_ui",
        "inventory_loot": "kp_player_ui",
        "death": "kp_player_ui",
        "replacement": "kp_player_ui",
        "late_join": "multi_ui",
        "player_leave": "multi_ui",
    }.get(coverage, "player_ui")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
