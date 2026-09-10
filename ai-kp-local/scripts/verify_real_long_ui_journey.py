"""Reconstruct and verify a real multi-episode UI journey without DB writes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ai_kp.evaluation.long_campaign_experience import (
    build_gm_persona_evidence,
    build_journey_coverage_evidence,
    build_player_persona_evidence,
    build_rps07_fault_evidence,
    build_rps_evidence,
)
from ai_kp.evaluation.long_ui_journey_reconstruction import (
    reconstruct_long_ui_journey_evidence,
)
from ai_kp.evaluation.ui_journey_reconstruction import load_playwright_evidence


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Cross-check long Playwright evidence against its SQLite authority "
            "database and immutable Session-End checkpoints."
        )
    )
    parser.add_argument("evidence", type=Path, help="Long Playwright evidence JSON")
    parser.add_argument("database", type=Path, help="SQLite authority database")
    parser.add_argument(
        "--rps-fault-evidence",
        type=Path,
        help="Optional Playwright JSON proving fail-closed player UI behavior",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write a new immutable verification report instead of stdout",
    )
    args = parser.parse_args()

    evidence_path = args.evidence.expanduser().resolve()
    playwright_evidence = load_playwright_evidence(evidence_path)
    result = reconstruct_long_ui_journey_evidence(
        playwright_evidence,
        args.database,
    )
    report = result.as_dict()
    report["persona_evidence_candidates"] = (
        build_player_persona_evidence(
            playwright_evidence,
            artifact_ref=str(evidence_path),
            artifact_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        )
        if result.accepted
        else []
    )
    report["journey_coverage_evidence_candidates"] = (
        build_journey_coverage_evidence(
            playwright_evidence,
            args.database,
            artifact_ref=str(evidence_path),
            artifact_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        )
        if result.accepted
        else []
    )
    report["gm_persona_evidence_candidates"] = (
        build_gm_persona_evidence(
            playwright_evidence,
            args.database,
            artifact_ref=str(evidence_path),
            artifact_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        )
        if result.accepted
        else []
    )
    rps_candidates = (
        build_rps_evidence(
            playwright_evidence,
            args.database,
            artifact_ref=str(evidence_path),
            artifact_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        )
        if result.accepted
        else []
    )
    if result.accepted and args.rps_fault_evidence is not None:
        fault_path = args.rps_fault_evidence.expanduser().resolve()
        fault_evidence = load_playwright_evidence(fault_path)
        rps_candidates.extend(build_rps07_fault_evidence(
            fault_evidence,
            artifact_ref=str(fault_path),
            artifact_sha256=hashlib.sha256(
                fault_path.read_bytes()
            ).hexdigest(),
        ))
    report["rps_evidence_candidates"] = rps_candidates
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        output = args.output.expanduser().resolve()
        if output.exists():
            raise FileExistsError(
                "Verification report already exists; choose a new immutable path"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(f"Immutable verification report written to {output}")
    return 0 if result.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
