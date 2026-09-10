import pytest

from ai_kp.platform.resolution.dice_expression import parse_dice_expression
from ai_kp.rulesets.coc7.mechanics.gameplay import evaluate_recorded_dice


def test_composite_expression_has_canonical_ast_and_term_evidence() -> None:
    parsed = parse_dice_expression(" 1D3 + 2d4 + 3 - 1 ")

    assert parsed.normalized == "1d3+2d4+2"
    assert parsed.dice_count == 3
    assert [(term.count, term.sides) for term in parsed.terms] == [(1, 3), (2, 4)]
    assert parsed.evaluate([2, 1, 4]) == (
        9,
        (
            {"term_index": 0, "expression": "1d3", "rolls": [2], "subtotal": 2},
            {"term_index": 1, "expression": "2d4", "rolls": [1, 4], "subtotal": 5},
        ),
    )


def test_legacy_single_term_and_fixed_expressions_still_replay() -> None:
    assert evaluate_recorded_dice("2d6+3", [2, 5]) == 10
    assert evaluate_recorded_dice("4", []) == 4
    assert evaluate_recorded_dice("1d3+1d4", [3, 4]) == 7


@pytest.mark.parametrize(
    "expression",
    (
        "1d3+damage_bonus",
        "1d3+伤害加值(1d4)",
        "1d3-1d4",
        "1d3;drop table",
        "1d3++1d4",
        "-1",
        "101d6",
        "1d1001",
        "1d2+1d2+1d2+1d2+1d2+1d2+1d2+1d2+1d2",
        "1+1+1+1+1+1+1+1+1",
        "1d6+10001",
    ),
)
def test_parser_rejects_variables_injection_and_limit_violations(expression: str) -> None:
    with pytest.raises(ValueError):
        parse_dice_expression(expression)


def test_each_term_validates_faces_against_its_own_sides() -> None:
    parsed = parse_dice_expression("1d3+1d8")
    with pytest.raises(ValueError, match="term 0"):
        parsed.evaluate([4, 1])
    with pytest.raises(ValueError, match="term 1"):
        parsed.evaluate([1, 9])
