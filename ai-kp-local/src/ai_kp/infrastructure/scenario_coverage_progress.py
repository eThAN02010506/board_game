"""Durable identity for bounded ScenarioContract coverage attempts.

The worker persists this identity after a supplement cycle makes no semantic
progress. A restart may reuse the rejected compilation only when the exact
executable contract, evidence authority, blocking targets, and server-owned
ending catalogs still match.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.platform.resolution.evidence_compiler import (
    EvidenceBoundCompilationResult,
    EvidenceBoundContractCandidate,
)
from ai_kp.platform.resolution.scenario_authoring import (
    ConstrainedScenarioContractAuthoringAdapter,
    ScenarioAuthoringEvidence,
    ScenarioContractReview,
)
from ai_kp.platform.resolution.source_coverage import SourceCoverageSupplementTarget


def coverage_attempt_fingerprint(
    adapter: ConstrainedScenarioContractAuthoringAdapter,
    candidate: EvidenceBoundContractCandidate,
    evidence: tuple[ScenarioAuthoringEvidence, ...],
    targets: tuple[SourceCoverageSupplementTarget, ...],
) -> str:
    """Identify one coverage problem and the authority able to solve it."""

    catalogs: list[dict[str, Any]] = []
    for group_evidence, group_targets in ScenarioContractService.coverage_supplement_groups(
        evidence, targets
    ):
        ending_targets = tuple(
            target
            for target in group_targets
            if target.requirement_key == "ending_rule"
        )
        ending_candidates: tuple[Any, ...] = ()
        ending_states: tuple[Any, ...] = ()
        if ending_targets:
            ending_candidates, ending_states = adapter.ending_catalogs(
                candidate.contract,
                group_evidence,
                ending_targets,
            )
        catalogs.append(
            {
                "source_block_ids": [item.source_block_id for item in group_evidence],
                "targets": [item.model_dump(mode="json") for item in group_targets],
                "ending_candidates": [
                    item.model_dump(mode="json") for item in ending_candidates
                ],
                "ending_states": [
                    item.model_dump(mode="json") for item in ending_states
                ],
            }
        )
    payload = {
        # Confidence and assumption prose cannot create an executable producer.
        # Metadata-only review churn must not reopen an impossible model task.
        "contract": candidate.contract.model_dump(mode="json"),
        "evidence_blocks": [
            item.model_dump(mode="json") for item in candidate.evidence_blocks
        ],
        "targets": [item.model_dump(mode="json") for item in targets],
        "catalogs": catalogs,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def restore_coverage_no_progress(
    guard: dict[str, Any] | None,
    *,
    adapter: ConstrainedScenarioContractAuthoringAdapter,
    candidate: EvidenceBoundContractCandidate,
    evidence: tuple[ScenarioAuthoringEvidence, ...],
) -> tuple[ScenarioContractReview, EvidenceBoundCompilationResult] | None:
    """Validate and restore one persisted fail-closed coverage result."""

    if not guard:
        return None
    try:
        targets = tuple(
            SourceCoverageSupplementTarget.model_validate(item)
            for item in guard["blocking_targets"]
        )
        review = ScenarioContractReview.model_validate(guard["review"])
        compilation = EvidenceBoundCompilationResult.model_validate(
            guard["compilation"]
        )
        fingerprint = coverage_attempt_fingerprint(
            adapter, candidate, evidence, targets
        )
    except (KeyError, TypeError, ValueError):
        return None
    if fingerprint != guard.get("fingerprint"):
        return None
    return review, compilation


__all__ = ["coverage_attempt_fingerprint", "restore_coverage_no_progress"]
