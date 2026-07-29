"""Deterministic golden-case evaluation and executable approval evidence."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from ai_kp.rule_authoring.engine import execute_rule
from ai_kp.rule_authoring.models import RuleGoldenCase, RuleObject
from ai_kp.rule_authoring.runtime import RuleExecutionError, validate_json_value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def evaluate_golden_cases(
    rule: RuleObject,
    cases: Iterable[RuleGoldenCase],
) -> dict[str, Any]:
    """Execute every KP-authored case and retain an auditable exact comparison."""

    results: list[dict[str, Any]] = []
    for case in cases:
        expected = case.expected_output
        try:
            validate_json_value(expected, label=f"golden case {case.name!r} expected output")
            actual = execute_rule(rule, case.inputs)
            passed = _canonical_json(actual) == _canonical_json(expected)
            result: dict[str, Any] = {
                "name": case.name,
                "inputs": case.inputs,
                "expected_output": expected,
                "actual_output": actual,
                "passed": passed,
            }
            if not passed:
                result["error"] = "actual output did not exactly match expected output"
        except RuleExecutionError as exc:
            result = {
                "name": case.name,
                "inputs": case.inputs,
                "expected_output": expected,
                "actual_output": None,
                "passed": False,
                "error": str(exc),
            }
        results.append(result)

    passed_count = sum(1 for result in results if result["passed"])
    return {
        "passed": bool(results) and passed_count == len(results),
        "case_count": len(results),
        "passed_count": passed_count,
        "comparison": "canonical_json_equality",
        "cases": results,
    }


def approval_allows_execution(record: dict[str, Any]) -> bool:
    """Fail closed unless validation evidence binds a KP review to this exact row."""

    if record.get("status") != "validated":
        return False
    validation = record.get("validation")
    if not isinstance(validation, dict) or validation.get("passed") is not True:
        return False
    review = validation.get("human_review")
    golden = validation.get("golden_tests")
    if not isinstance(review, dict) or not isinstance(golden, dict):
        return False
    if review.get("decision") != "approved" or not review.get("reviewer_member_id"):
        return False
    reviewed_at = review.get("reviewed_at")
    if not isinstance(reviewed_at, str) or not reviewed_at.strip():
        return False
    if review.get("reviewed_object_hash") != record.get("object_hash"):
        return False
    case_count = golden.get("case_count")
    passed_count = golden.get("passed_count")
    if type(case_count) is not int or type(passed_count) is not int:
        return False
    cases = golden.get("cases")
    if not isinstance(cases, list):
        return False
    return (
        golden.get("passed") is True
        and case_count > 0
        and passed_count == case_count
        and all(
            isinstance(case, dict) and case.get("passed") is True
            for case in cases
        )
        and len(cases) == case_count
    )


__all__ = ["approval_allows_execution", "evaluate_golden_cases"]
