"""Assemble AC-LONG release evidence from immutable, independently verified files."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from ai_kp.evaluation.long_campaign_evidence import evaluate_long_campaign_evidence

_CANDIDATE_FIELDS = (
    "persona_evidence_candidates",
    "gm_persona_evidence_candidates",
    "rps_evidence_candidates",
    "journey_coverage_evidence_candidates",
)


class LongCampaignReleaseError(ValueError):
    """Raised when independently produced release evidence cannot be trusted."""


def assemble_long_campaign_release(
    foundation: Mapping[str, Any],
    journey_report: Mapping[str, Any],
    repair_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a complete release envelope or fail before producing a claim."""

    source_commit = foundation.get("source_commit")
    strict = journey_report.get("strict_evidence")
    verification = journey_report.get("verification")
    if journey_report.get("accepted") is not True:
        raise LongCampaignReleaseError("long UI reconstruction report is not accepted")
    if not isinstance(strict, Mapping) or strict.get("source_commit") != source_commit:
        raise LongCampaignReleaseError(
            "foundation and long UI reconstruction must use the same source commit"
        )
    if not isinstance(verification, Mapping) or verification.get("accepted") is not True:
        raise LongCampaignReleaseError("strict long UI verifier is not accepted")
    metrics = verification.get("metrics")
    if not isinstance(metrics, Mapping):
        raise LongCampaignReleaseError("strict long UI metrics are missing")
    if metrics.get("players") != 4 or _integer(metrics.get("ended_episodes")) < 10:
        raise LongCampaignReleaseError(
            "long UI reconstruction must prove four players and ten ended episodes"
        )
    if repair_report.get("source_commit") != source_commit:
        raise LongCampaignReleaseError(
            "repair report and foundation must use the same source commit"
        )

    persona = _candidate_list(journey_report, "persona_evidence_candidates")
    persona.extend(_candidate_list(journey_report, "gm_persona_evidence_candidates"))
    rps = _candidate_list(journey_report, "rps_evidence_candidates")
    coverage = _candidate_list(
        journey_report, "journey_coverage_evidence_candidates"
    )
    _require_unique(persona, "persona_id")
    _require_unique(rps, "requirement_id")
    _require_unique(coverage, "coverage_id")
    for field in _CANDIDATE_FIELDS:
        for record in _candidate_list(journey_report, field):
            _verify_artifact(record, "artifact_ref", "artifact_sha256")

    rounds = repair_report.get("rounds")
    if not isinstance(rounds, list):
        raise LongCampaignReleaseError("repair report rounds must be a list")
    normalized_rounds: list[dict[str, Any]] = []
    for index, raw in enumerate(rounds):
        if not isinstance(raw, Mapping):
            raise LongCampaignReleaseError(f"repair round {index + 1} must be an object")
        _verify_artifact(raw, "evidence_ref", "evidence_sha256")
        normalized_rounds.append(dict(raw))

    result = deepcopy(dict(foundation))
    result.pop("gate", None)
    result["scripted_sessions"] = _integer(metrics.get("ended_episodes"))
    result["persona_evidence"] = persona
    result["rps_evidence"] = rps
    result["journey_coverage_evidence"] = coverage
    result["repair_rounds"] = normalized_rounds
    result["ui_journey_passed"] = True
    result["four_player_full_ai_passed"] = True
    result["restart_recovery_passed"] = (
        foundation.get("restart_recovery_passed") is True
        and metrics.get("restart_checkpoint_accepted") is True
    )
    result["open_defects"] = deepcopy(repair_report.get("open_defects"))
    result["release_inputs"] = {
        "long_ui_journey_id": metrics.get("journey_id"),
        "long_ui_campaign_id": metrics.get("campaign_id"),
        "repair_report_sha256": repair_report.get("report_sha256"),
    }
    gate = evaluate_long_campaign_evidence(result)
    result["gate"] = gate.as_dict()
    if not gate.ready:
        raise LongCampaignReleaseError(
            "AC-LONG release gate remains blocked: " + "; ".join(gate.blockers)
        )
    return result


def _candidate_list(report: Mapping[str, Any], field: str) -> list[dict[str, Any]]:
    value = report.get(field)
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise LongCampaignReleaseError(f"{field} must be a list of objects")
    return [dict(item) for item in value]


def _require_unique(records: list[dict[str, Any]], key: str) -> None:
    values = [record.get(key) for record in records]
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise LongCampaignReleaseError(f"every merged record requires {key}")
    if len(values) != len(set(values)):
        raise LongCampaignReleaseError(f"duplicate merged {key} records are forbidden")


def _verify_artifact(record: Mapping[str, Any], ref_key: str, digest_key: str) -> None:
    raw_path = record.get(ref_key)
    expected = record.get(digest_key)
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise LongCampaignReleaseError(f"missing {ref_key}")
    if not isinstance(expected, str):
        raise LongCampaignReleaseError(f"missing {digest_key} for {raw_path}")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise LongCampaignReleaseError(f"{ref_key} must be an absolute path: {raw_path}")
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise LongCampaignReleaseError(f"cannot read evidence artifact: {path}") from exc
    if actual != expected:
        raise LongCampaignReleaseError(f"evidence artifact digest mismatch: {path}")


def _integer(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


__all__ = ["LongCampaignReleaseError", "assemble_long_campaign_release"]
