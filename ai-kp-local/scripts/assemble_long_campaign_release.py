"""Create one immutable AC-LONG release envelope from verified artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from ai_kp.evaluation.long_campaign_release import assemble_long_campaign_release


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed assembler for the final AC-LONG release evidence"
    )
    parser.add_argument("foundation", type=Path)
    parser.add_argument("journey_report", type=Path)
    parser.add_argument("repair_report", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise FileExistsError("Release evidence already exists; choose a new immutable path")

    foundation_path = args.foundation.expanduser().resolve()
    journey_path = args.journey_report.expanduser().resolve()
    repair_path = args.repair_report.expanduser().resolve()
    foundation = _object(foundation_path)
    journey = _object(journey_path)
    repairs = _object(repair_path)
    repairs["report_sha256"] = _digest(repair_path)
    result = assemble_long_campaign_release(foundation, journey, repairs)
    result["release_inputs"].update({
        "foundation_ref": str(foundation_path),
        "foundation_sha256": _digest(foundation_path),
        "journey_report_ref": str(journey_path),
        "journey_report_sha256": _digest(journey_path),
        "repair_report_ref": str(repair_path),
        "repair_report_sha256": _digest(repair_path),
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["gate"], ensure_ascii=False, indent=2))
    print(f"Immutable AC-LONG release evidence written to {output}")
    return 0


def _object(path: Path) -> dict[str, Any]:
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON input must be an object: {path}")
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
