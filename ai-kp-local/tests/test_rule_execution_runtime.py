import unittest

from pydantic import ValidationError

from ai_kp.rule_authoring.engine import RuleExecutionError, execute_rule
from ai_kp.rule_authoring.models import RuleObject


def _rule(execution: dict) -> RuleObject:
    return RuleObject.model_validate(
        {
            "rule_key": "test.runtime.rule",
            "title": "Runtime test",
            "summary": "Exercises the closed deterministic runtime.",
            "execution": execution,
            "citations": [
                {
                    "chunk_id": "chunk-test",
                    "page": 1,
                    "evidence_text": "Synthetic test evidence.",
                }
            ],
            "confidence": 1,
        }
    )


class RuleExecutionRuntimeTests(unittest.TestCase):
    def test_condition_effect_strategy_applies_nested_operations(self) -> None:
        rule = _rule(
            {
                "kind": "condition_effects",
                "inputs": ["actor.hp", "damage"],
                "branches": [
                    {
                        "label": "take-damage",
                        "all": [
                            {
                                "left": {"field": "damage"},
                                "operator": "gt",
                                "right": {"value": 0},
                            }
                        ],
                        "effects": [
                            {
                                "target": "actor.hp",
                                "operation": "subtract",
                                "operand": {"field": "damage"},
                            },
                            {
                                "target": "actor.injured",
                                "operation": "set",
                                "operand": {"value": True},
                            },
                        ],
                    }
                ],
            }
        )

        result = execute_rule(rule, {"actor": {"hp": 10}, "damage": 3})

        self.assertEqual(result["actor"]["hp"], 7)
        self.assertTrue(result["actor"]["injured"])
        self.assertEqual(result["_matched_branches"], ["take-damage"])

    def test_lookup_strategy_fails_closed_when_no_row_matches(self) -> None:
        rule = _rule(
            {
                "kind": "lookup_table",
                "inputs": ["roll"],
                "lookup_input": "roll",
                "rows": [
                    {
                        "minimum": 1,
                        "maximum": 20,
                        "output": {"outcome": "low"},
                    }
                ],
            }
        )

        with self.assertRaisesRegex(RuleExecutionError, "No lookup row"):
            execute_rule(rule, {"roll": 80})

    def test_non_set_effect_requires_existing_target(self) -> None:
        rule = _rule(
            {
                "kind": "condition_effects",
                "branches": [
                    {
                        "effects": [
                            {
                                "target": "actor.hp",
                                "operation": "add",
                                "operand": {"value": 1},
                            }
                        ]
                    }
                ],
            }
        )

        with self.assertRaisesRegex(RuleExecutionError, "Effect target is missing"):
            execute_rule(rule, {"actor": {}})

    def test_lookup_range_type_error_is_normalized(self) -> None:
        rule = _rule(
            {
                "kind": "lookup_table",
                "lookup_input": "roll",
                "rows": [
                    {
                        "minimum": 1,
                        "maximum": 20,
                        "output": {"outcome": "low"},
                    }
                ],
            }
        )

        with self.assertRaisesRegex(
            RuleExecutionError, "Incompatible lookup input"
        ):
            execute_rule(rule, {"roll": "not-a-number"})

    def test_non_json_input_is_rejected_before_copying(self) -> None:
        class ActiveObject:
            copied = False

            def __deepcopy__(self, _memo):
                self.copied = True
                raise AssertionError("executor must not invoke user-defined copy hooks")

        active = ActiveObject()
        rule = _rule(
            {
                "kind": "condition_effects",
                "branches": [
                    {
                        "effects": [
                            {
                                "target": "complete",
                                "operation": "set",
                                "operand": {"value": True},
                            }
                        ]
                    }
                ],
            }
        )

        with self.assertRaisesRegex(RuleExecutionError, "JSON-compatible"):
            execute_rule(rule, {"untrusted": active})
        self.assertFalse(active.copied)

    def test_execution_rejects_excessive_input_depth(self) -> None:
        nested: dict = {}
        current = nested
        for _ in range(33):
            child: dict = {}
            current["child"] = child
            current = child
        rule = _rule(
            {
                "kind": "condition_effects",
                "branches": [
                    {
                        "effects": [
                            {
                                "target": "complete",
                                "operation": "set",
                                "operand": {"value": True},
                            }
                        ]
                    }
                ],
            }
        )

        with self.assertRaisesRegex(RuleExecutionError, "depth limit"):
            execute_rule(rule, nested)

    def test_effect_rejects_non_finite_result(self) -> None:
        rule = _rule(
            {
                "kind": "condition_effects",
                "branches": [
                    {
                        "effects": [
                            {
                                "target": "value",
                                "operation": "add",
                                "operand": {"value": 1e308},
                            }
                        ]
                    }
                ],
            }
        )

        with self.assertRaisesRegex(RuleExecutionError, "non-finite"):
            execute_rule(rule, {"value": 1e308})

    def test_rule_schema_rejects_unknown_fields_and_malformed_paths(self) -> None:
        execution = {
            "kind": "condition_effects",
            "inputs": ["actor..hp"],
            "branches": [
                {
                    "effects": [
                        {
                            "target": "complete",
                            "operation": "set",
                            "operand": {"value": True},
                        }
                    ]
                }
            ],
            "python_expression": "dangerous()",
        }

        with self.assertRaises(ValidationError):
            _rule(execution)

    def test_rule_schema_bounds_total_execution_operations(self) -> None:
        branches = [
            {
                "all": [
                    {
                        "left": {"field": "value"},
                        "operator": "eq",
                        "right": {"value": index},
                    }
                    for index in range(4)
                ],
                "effects": [
                    {
                        "target": "matched",
                        "operation": "set",
                        "operand": {"value": True},
                    }
                ],
            }
            for _ in range(103)
        ]

        with self.assertRaisesRegex(ValidationError, "512 operations"):
            _rule({"kind": "condition_effects", "branches": branches})


if __name__ == "__main__":
    unittest.main()
