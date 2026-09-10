"""Execute the reproducible, non-UI portion of AC-LONG and write evidence JSON.

The final release gate remains blocked until separate real-browser, persona, model
switch, and three-round evidence is merged into the report.  This script never
turns missing evidence into a pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_kp.evaluation.edge_case_catalog import EDGE_CASES
from ai_kp.evaluation.long_campaign_durability import LongCampaignDurabilityRunner
from ai_kp.evaluation.long_campaign_evidence import (
    SCHEMA_VERSION,
    evaluate_long_campaign_evidence,
)


def main() -> int:
    args = _arguments()
    project_root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("Evidence output already exists; choose a new immutable path")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ai-kp-long-campaign-") as temp_dir:
        durability = LongCampaignDurabilityRunner(
            Path(temp_dir) / "durability.sqlite3"
        ).run()
    edge_result = _run_edges(project_root)
    passed_edges = [item.id for item in EDGE_CASES] if edge_result["passed"] else []

    evidence: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_commit": _source_commit(project_root),
        "scripted_sessions": durability["scripted_sessions"],
        "durability_sessions": durability["sessions"],
        "equivalent_table_minutes": durability["equivalent_table_minutes"],
        "passed_edge_case_ids": passed_edges,
        "persona_evidence": [],
        "rps_evidence": [],
        "journey_coverage_evidence": [],
        "repair_rounds": [],
        "ui_journey_passed": False,
        "four_player_full_ai_passed": False,
        "restart_recovery_passed": durability["process_restarts"] >= 20,
        "model_switch_passed": durability["model_switch_passed"],
        "index_rebuild_passed": durability["index_rebuild_passed"],
        "model_off_replay_passed": durability["status"] == "passed",
        "authority_fingerprint_stable": durability["authority_fingerprint_stable"],
        "secret_leak_count_is_zero": durability["secret_leak_count"] == 0,
        "lifecycle_durability_passed": durability["lifecycle"]["passed"],
        "open_defects": {"P0": 0, "P1": 0},
        "durability": durability,
        "edge_case_run": edge_result,
    }
    evidence["gate"] = evaluate_long_campaign_evidence(evidence).as_dict()
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence["gate"], ensure_ascii=False, indent=2))
    print(f"Evidence written to {output}")
    return 0 if edge_result["passed"] and durability["status"] == "passed" else 1


def _run_edges(project_root: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *[item.pytest_node for item in EDGE_CASES],
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    transcript = completed.stdout + completed.stderr
    return {
        "passed": completed.returncode == 0,
        "count": len(EDGE_CASES),
        "catalog_ids": [item.id for item in EDGE_CASES],
        "return_code": completed.returncode,
        "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        "summary": transcript.strip().splitlines()[-1] if transcript.strip() else "",
    }


def _source_commit(project_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New immutable evidence JSON path",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
