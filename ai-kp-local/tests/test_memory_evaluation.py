from pathlib import Path

from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import db_session
from ai_kp.platform.memory.evaluation import (
    MemoryEvaluationCase,
    evaluate_memory_retrieval,
)
from ai_kp.platform.memory.retrieval import MemoryRetriever


def test_memory_evaluation_reports_recall_rank_and_spoiler_leakage(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "evaluation.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("评测团")
        public = repo.add_memory(
            campaign_id=campaign["id"],
            scope="clue",
            visibility="table",
            text="旧码头仓库的侧门留有新鲜刮痕。",
        )
        secret = repo.add_memory(
            campaign_id=campaign["id"],
            scope="clue",
            visibility="kp",
            text="旧码头仓库地下藏着仪式祭坛。",
        )
        report = evaluate_memory_retrieval(
            MemoryRetriever(connection),
            (
                MemoryEvaluationCase(
                    case_id="public-clue",
                    query="调查旧码头仓库",
                    campaign_id=campaign["id"],
                    visibility=("table",),
                    expected_ids=(public["id"],),
                    forbidden_ids=(secret["id"],),
                ),
            ),
        )

    assert report.case_count == 1
    assert report.mean_recall_at_k == 1.0
    assert report.mean_reciprocal_rank == 1.0
    assert report.forbidden_hit_count == 0
    assert report.results[0].returned_ids == (public["id"],)
