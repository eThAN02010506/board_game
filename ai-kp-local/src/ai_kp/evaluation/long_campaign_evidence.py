"""Fail-closed release evidence for the long-campaign product standard.

This module deliberately does not run the game.  It validates evidence produced by
independent UI, durability, edge-case, replay, and defect-review runners.  Keeping
the release decision separate from every producer prevents one successful model
call or one large unit-test count from being presented as AC-LONG completion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

REQUIRED_PERSONAS = frozenset(
    {
        "trpg_newcomer",
        "rules_veteran",
        "roleplayer",
        "optimizer",
        "chaos_sandbox_player",
        "new_gm",
        "veteran_gm",
        "ai_assisted_gm",
    }
)
REQUIRED_RPS = frozenset(f"RPS-{index:02d}" for index in range(1, 13))
REQUIRED_JOURNEY_COVERAGE = frozenset(
    {
        "exploration",
        "npc_dialogue",
        "investigation",
        "successful_check",
        "failed_check",
        "combat",
        "inventory_loot",
        "improvised_action",
        "major_choice",
        "growth",
        "death",
        "replacement",
        "late_join",
        "player_leave",
    }
)
SCHEMA_VERSION = 2
MINIMUM_EDGE_CASES = 50
MINIMUM_SCRIPTED_SESSIONS = 10
MINIMUM_DURABILITY_SESSIONS = 20
MINIMUM_TABLE_MINUTES = 50 * 60
MINIMUM_REPAIR_ROUNDS = 3
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
_PLAYER_PERSONAS = REQUIRED_PERSONAS - {"new_gm", "veteran_gm", "ai_assisted_gm"}
_PERSONA_EVIDENCE_KEYS = frozenset(
    {
        "persona_id",
        "actor_mode",
        "source_commit",
        "observed_at",
        "artifact_ref",
        "artifact_sha256",
        "observation_ids",
        "interaction_count",
    }
)
_RPS_EVIDENCE_KEYS = frozenset(
    {
        "requirement_id",
        "surface",
        "source_commit",
        "observed_at",
        "artifact_ref",
        "artifact_sha256",
        "observation_ids",
    }
)
_COVERAGE_EVIDENCE_KEYS = frozenset(
    {
        "coverage_id",
        "surface",
        "source_commit",
        "observed_at",
        "artifact_ref",
        "artifact_sha256",
        "observation_ids",
    }
)
_COVERAGE_SURFACES = {
    "combat": "kp_player_ui",
    "inventory_loot": "kp_player_ui",
    "death": "kp_player_ui",
    "replacement": "kp_player_ui",
    "late_join": "multi_ui",
    "player_leave": "multi_ui",
}


@dataclass(frozen=True)
class LongCampaignGateResult:
    ready: bool
    blockers: tuple[str, ...]
    metrics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "blockers": list(self.blockers),
            "metrics": self.metrics,
        }


def evaluate_long_campaign_evidence(evidence: dict[str, Any]) -> LongCampaignGateResult:
    """Evaluate AC-LONG without inferring or silently defaulting missing evidence."""

    blockers: list[str] = []
    if evidence.get("schema_version") != SCHEMA_VERSION:
        blockers.append(f"schema_version must equal {SCHEMA_VERSION}")
    source_commit = evidence.get("source_commit")
    if not isinstance(source_commit, str) or not _COMMIT.fullmatch(source_commit):
        blockers.append("source_commit is missing or invalid")
        source_commit = None
    scripted_sessions = _non_negative_int(evidence.get("scripted_sessions"))
    durability_sessions = _non_negative_int(evidence.get("durability_sessions"))
    table_minutes = _non_negative_int(evidence.get("equivalent_table_minutes"))
    edge_cases = _unique_strings(evidence.get("passed_edge_case_ids"))
    personas = _valid_persona_evidence(
        evidence.get("persona_evidence"), source_commit=source_commit
    )
    rps = _valid_rps_evidence(
        evidence.get("rps_evidence"), source_commit=source_commit
    )
    journey_coverage = _valid_journey_coverage_evidence(
        evidence.get("journey_coverage_evidence"), source_commit=source_commit
    )
    rounds = _valid_rounds(evidence.get("repair_rounds"))

    _minimum(blockers, "scripted Sessions", scripted_sessions, MINIMUM_SCRIPTED_SESSIONS)
    _minimum(
        blockers,
        "durability Sessions",
        durability_sessions,
        MINIMUM_DURABILITY_SESSIONS,
    )
    _minimum(blockers, "equivalent table minutes", table_minutes, MINIMUM_TABLE_MINUTES)
    _minimum(blockers, "unique edge cases", len(edge_cases), MINIMUM_EDGE_CASES)
    _minimum(blockers, "repair/retest rounds", len(rounds), MINIMUM_REPAIR_ROUNDS)

    missing_personas = sorted(REQUIRED_PERSONAS - personas)
    if missing_personas:
        blockers.append("missing persona evidence: " + ", ".join(missing_personas))
    missing_rps = sorted(REQUIRED_RPS - rps)
    if missing_rps:
        blockers.append("missing Real Player Standard evidence: " + ", ".join(missing_rps))
    missing_coverage = sorted(REQUIRED_JOURNEY_COVERAGE - journey_coverage)
    if missing_coverage:
        blockers.append("missing scripted journey coverage: " + ", ".join(missing_coverage))

    boolean_requirements = {
        "player UI setup-to-continue journey": "ui_journey_passed",
        "four-player Full AI journey": "four_player_full_ai_passed",
        "process restart recovery": "restart_recovery_passed",
        "model-switch recovery": "model_switch_passed",
        "memory index rebuild": "index_rebuild_passed",
        "model-off deterministic replay": "model_off_replay_passed",
        "authority fingerprint stability": "authority_fingerprint_stable",
        "secret isolation": "secret_leak_count_is_zero",
        "multi-transition character lifecycle durability": (
            "lifecycle_durability_passed"
        ),
    }
    for label, key in boolean_requirements.items():
        if evidence.get(key) is not True:
            blockers.append(f"missing or failed {label}")

    open_defects = evidence.get("open_defects")
    if not isinstance(open_defects, dict):
        blockers.append("open defect counts are missing")
        p0 = p1 = -1
    else:
        p0 = _non_negative_int(open_defects.get("P0"))
        p1 = _non_negative_int(open_defects.get("P1"))
        if p0 != 0:
            blockers.append("open P0 defects must be zero")
        if p1 != 0:
            blockers.append("open P1 defects must be zero")

    metrics = {
        "scripted_sessions": scripted_sessions,
        "durability_sessions": durability_sessions,
        "equivalent_table_minutes": table_minutes,
        "unique_edge_cases": len(edge_cases),
        "validated_personas": len(personas & REQUIRED_PERSONAS),
        "validated_rps": len(rps & REQUIRED_RPS),
        "validated_journey_coverage": len(
            journey_coverage & REQUIRED_JOURNEY_COVERAGE
        ),
        "repair_rounds": len(rounds),
        "open_p0": p0,
        "open_p1": p1,
    }
    return LongCampaignGateResult(
        ready=not blockers,
        blockers=tuple(blockers),
        metrics=metrics,
    )


def _minimum(blockers: list[str], label: str, actual: int, expected: int) -> None:
    if actual < expected:
        blockers.append(f"{label}: {actual}/{expected}")


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _unique_strings(value: Any) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str) and item.strip())


def _valid_persona_evidence(
    value: Any, *, source_commit: str | None
) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    valid: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != _PERSONA_EVIDENCE_KEYS:
            continue
        persona_id = item.get("persona_id")
        expected_mode = "player_ui" if persona_id in _PLAYER_PERSONAS else "gm_ui"
        if (
            persona_id not in REQUIRED_PERSONAS
            or item.get("actor_mode") != expected_mode
            or not _valid_provenance(item)
            or item.get("source_commit") != source_commit
            or _non_negative_int(item.get("interaction_count")) < 1
        ):
            continue
        valid.add(persona_id)
    return frozenset(valid)


def _valid_rps_evidence(value: Any, *, source_commit: str | None) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    valid: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != _RPS_EVIDENCE_KEYS:
            continue
        requirement_id = item.get("requirement_id")
        required_surface = "gm_ui" if requirement_id == "RPS-06" else "player_ui"
        if (
            requirement_id not in REQUIRED_RPS
            or item.get("surface") != required_surface
            or not _valid_provenance(item)
            or item.get("source_commit") != source_commit
        ):
            continue
        valid.add(requirement_id)
    return frozenset(valid)


def _valid_journey_coverage_evidence(
    value: Any, *, source_commit: str | None
) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    valid: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != _COVERAGE_EVIDENCE_KEYS:
            continue
        coverage_id = item.get("coverage_id")
        required_surface = _COVERAGE_SURFACES.get(coverage_id, "player_ui")
        if (
            coverage_id not in REQUIRED_JOURNEY_COVERAGE
            or item.get("surface") != required_surface
            or not _valid_provenance(item)
            or item.get("source_commit") != source_commit
        ):
            continue
        valid.add(coverage_id)
    return frozenset(valid)


def _valid_provenance(item: dict[str, Any]) -> bool:
    source_commit = item.get("source_commit")
    observed_at = item.get("observed_at")
    artifact_ref = item.get("artifact_ref")
    artifact_sha256 = item.get("artifact_sha256")
    observation_ids = item.get("observation_ids")
    if not isinstance(source_commit, str) or not _COMMIT.fullmatch(source_commit):
        return False
    if not isinstance(observed_at, str):
        return False
    try:
        parsed = datetime.fromisoformat(observed_at)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    return (
        isinstance(artifact_ref, str)
        and bool(artifact_ref.strip())
        and isinstance(artifact_sha256, str)
        and _SHA256.fullmatch(artifact_sha256) is not None
        and isinstance(observation_ids, list)
        and bool(observation_ids)
        and len(observation_ids) == len(set(observation_ids))
        and all(isinstance(value, str) and value.strip() for value in observation_ids)
    )


def _valid_rounds(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    valid: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        round_id = item.get("id")
        if not isinstance(round_id, str) or not round_id.strip() or round_id in seen:
            continue
        if item.get("full_retest_passed") is not True:
            continue
        if not isinstance(item.get("input_version"), str):
            continue
        if not isinstance(item.get("evidence_ref"), str):
            continue
        if not isinstance(item.get("remaining_risks"), list):
            continue
        seen.add(round_id)
        valid.append(item)
    return tuple(valid)


__all__ = [
    "MINIMUM_DURABILITY_SESSIONS",
    "MINIMUM_EDGE_CASES",
    "MINIMUM_REPAIR_ROUNDS",
    "MINIMUM_SCRIPTED_SESSIONS",
    "MINIMUM_TABLE_MINUTES",
    "REQUIRED_JOURNEY_COVERAGE",
    "REQUIRED_PERSONAS",
    "REQUIRED_RPS",
    "SCHEMA_VERSION",
    "LongCampaignGateResult",
    "evaluate_long_campaign_evidence",
]
