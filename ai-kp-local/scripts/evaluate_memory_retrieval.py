"""Evaluate local memory retrieval against an ID-anchored JSON fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.memory.evaluation import (
    MemoryEvaluationCase,
    evaluate_memory_retrieval,
)
from ai_kp.platform.memory.retrieval import MemoryRetriever


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--db", type=Path, default=Path("data/ai_kp.sqlite3"))
    args = parser.parse_args()
    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        parser.error("fixture must contain a JSON array")
    cases = tuple(
        MemoryEvaluationCase(
            case_id=str(item["case_id"]),
            query=str(item["query"]),
            campaign_id=str(item["campaign_id"]),
            pc_id=str(item["pc_id"]) if item.get("pc_id") else None,
            visibility=tuple(item.get("visibility", ("table", "kp"))),
            expected_ids=tuple(item.get("expected_ids", ())),
            forbidden_ids=tuple(item.get("forbidden_ids", ())),
            top_k=int(item.get("top_k", 8)),
        )
        for item in payload
    )
    connection = connect(args.db)
    try:
        init_db(connection)
        report = evaluate_memory_retrieval(MemoryRetriever(connection), cases)
    finally:
        connection.close()
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 1 if report.forbidden_hit_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
