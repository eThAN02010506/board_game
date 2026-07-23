import unittest

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


if __name__ == "__main__":
    unittest.main()
