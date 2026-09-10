"""Reconstruct and verify one real Playwright UI journey without database writes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_kp.evaluation.ui_journey_reconstruction import (
    load_playwright_evidence,
    reconstruct_ui_journey_evidence,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-check Playwright evidence against its SQLite authority database."
    )
    parser.add_argument("evidence", type=Path, help="Playwright evidence JSON")
    parser.add_argument("database", type=Path, help="SQLite authority database")
    parser.add_argument(
        "--campaign-id",
        help="Optional DB locator; it cannot replace a missing Playwright campaign_id proof",
    )
    args = parser.parse_args()

    evidence = load_playwright_evidence(args.evidence)
    result = reconstruct_ui_journey_evidence(
        evidence,
        args.database,
        campaign_id=args.campaign_id,
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.verification.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
