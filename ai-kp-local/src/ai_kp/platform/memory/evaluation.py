"""Deterministic retrieval evaluation for campaign-specific memory fixtures."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ai_kp.observability import operational_telemetry
from ai_kp.platform.memory.retrieval import MemoryRetriever


@dataclass(frozen=True)
class MemoryEvaluationCase:
    case_id: str
    query: str
    campaign_id: str
    expected_ids: tuple[str, ...]
    forbidden_ids: tuple[str, ...] = ()
    pc_id: str | None = None
    visibility: tuple[str, ...] = ("table", "kp")
    top_k: int = 8


@dataclass(frozen=True)
class MemoryEvaluationResult:
    case_id: str
    returned_ids: tuple[str, ...]
    recall_at_k: float
    reciprocal_rank: float
    forbidden_hits: tuple[str, ...]


@dataclass(frozen=True)
class MemoryEvaluationReport:
    case_count: int
    mean_recall_at_k: float
    mean_reciprocal_rank: float
    forbidden_hit_count: int
    results: tuple[MemoryEvaluationResult, ...]

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "results": [asdict(result) for result in self.results],
        }


def evaluate_memory_retrieval(
    retriever: MemoryRetriever,
    cases: tuple[MemoryEvaluationCase, ...],
) -> MemoryEvaluationReport:
    results: list[MemoryEvaluationResult] = []
    for case in cases:
        returned = retriever.retrieve(
            case.query,
            campaign_id=case.campaign_id,
            pc_id=case.pc_id,
            visibility=case.visibility,
            limit=case.top_k,
        )
        returned_ids = tuple(item.id for item in returned)
        expected = set(case.expected_ids)
        hits = expected.intersection(returned_ids)
        recall = len(hits) / len(expected) if expected else 1.0
        first_rank = next(
            (index for index, item_id in enumerate(returned_ids, start=1) if item_id in expected),
            None,
        )
        reciprocal_rank = 1.0 / first_rank if first_rank is not None else 0.0
        forbidden_hits = tuple(
            item_id for item_id in returned_ids if item_id in set(case.forbidden_ids)
        )
        results.append(
            MemoryEvaluationResult(
                case_id=case.case_id,
                returned_ids=returned_ids,
                recall_at_k=recall,
                reciprocal_rank=reciprocal_rank,
                forbidden_hits=forbidden_hits,
            )
        )

    count = len(results)
    report = MemoryEvaluationReport(
        case_count=count,
        mean_recall_at_k=(
            sum(result.recall_at_k for result in results) / count if count else 0.0
        ),
        mean_reciprocal_rank=(
            sum(result.reciprocal_rank for result in results) / count if count else 0.0
        ),
        forbidden_hit_count=sum(len(result.forbidden_hits) for result in results),
        results=tuple(results),
    )
    operational_telemetry.record_retrieval(report.to_dict())
    return report
