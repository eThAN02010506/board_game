"""Citation, schema, conflict, and status validation for rule candidates."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Protocol

from ai_kp.rule_authoring.models import RuleObject, RuleStatus


class RuleValidationStore(Protocol):
    def get_rule_chunk(self, chunk_id: str) -> dict:
        ...

    def list_rule_objects(
        self,
        source_id: str,
        *,
        status: str | None = None,
        rule_key: str | None = None,
    ) -> list[dict]:
        ...


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


class RuleValidator:
    """Three gates: schema, source citation, then conflict/consistency."""

    def __init__(self, repo: RuleValidationStore):
        self.repo = repo

    def validate(self, source_id: str, candidate: dict[str, Any]) -> tuple[RuleObject, dict]:
        report: dict[str, Any] = {
            "schema": {"passed": False, "errors": []},
            "citations": {"passed": False, "errors": []},
            "conflicts": {"passed": False, "errors": []},
        }
        rule = RuleObject.model_validate(candidate)
        report["schema"]["passed"] = True

        for citation in rule.citations:
            try:
                chunk = self.repo.get_rule_chunk(citation.chunk_id)
            except KeyError:
                report["citations"]["errors"].append(
                    f"unknown chunk: {citation.chunk_id}"
                )
                continue
            if chunk["source_id"] != source_id:
                report["citations"]["errors"].append(
                    f"chunk belongs to another source: {citation.chunk_id}"
                )
            if not chunk["page_start"] <= citation.page <= chunk["page_end"]:
                report["citations"]["errors"].append(
                    f"page {citation.page} is outside chunk {citation.chunk_id}"
                )
            if _normalized(citation.evidence_text) not in _normalized(chunk["text"]):
                report["citations"]["errors"].append(
                    f"evidence is not present in chunk {citation.chunk_id}"
                )
            else:
                citation.evidence_hash = hashlib.sha256(
                    citation.evidence_text.strip().encode("utf-8")
                ).hexdigest()
        report["citations"]["passed"] = not report["citations"]["errors"]

        canonical = json.dumps(
            rule.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        candidate_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        existing = self.repo.list_rule_objects(
            source_id, status=RuleStatus.VALIDATED.value, rule_key=rule.rule_key
        )
        differing = [item for item in existing if item["object_hash"] != candidate_hash]
        if differing:
            report["conflicts"]["errors"].append(
                f"validated rule with key {rule.rule_key} already has different content"
            )
        report["conflicts"]["passed"] = not report["conflicts"]["errors"]
        report["passed"] = all(report[layer]["passed"] for layer in ("schema", "citations", "conflicts"))
        return rule, report

    @staticmethod
    def status_for(rule: RuleObject, report: dict) -> RuleStatus:
        if report["conflicts"]["errors"]:
            return RuleStatus.QUARANTINED
        if not report["passed"] or rule.confidence < 0.75:
            return RuleStatus.REVIEW_REQUIRED
        return RuleStatus.VALIDATED
