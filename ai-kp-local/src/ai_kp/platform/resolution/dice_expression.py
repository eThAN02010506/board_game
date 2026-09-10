"""Ruleset-neutral bounded dice expressions with replayable term evidence.

This module deliberately accepts only dice literals and integer constants.  Source
vocabulary such as "damage bonus" belongs to the ruleset effect catalog and must be
resolved to a concrete die term before an expression reaches this parser.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

MAX_TERMS = 8
MAX_TOTAL_DICE = 100
MAX_SIDES = 1_000
MAX_ABS_MODIFIER = 10_000

_TERM = re.compile(r"(?P<count>[1-9]\d*)[dD](?P<sides>[1-9]\d*)|(?P<integer>\d+)")


@dataclass(frozen=True, slots=True)
class DiceTerm:
    count: int
    sides: int

    def __post_init__(self) -> None:
        if type(self.count) is not int or not 1 <= self.count <= MAX_TOTAL_DICE:
            raise ValueError("Dice term count exceeds safety limits")
        if type(self.sides) is not int or not 1 <= self.sides <= MAX_SIDES:
            raise ValueError("Dice term sides exceed safety limits")

    @property
    def normalized(self) -> str:
        return f"{self.count}d{self.sides}"


@dataclass(frozen=True, slots=True)
class AdditiveDiceExpression:
    """Canonical AST for ``dice (+ dice)* (+/- integer)*``."""

    terms: tuple[DiceTerm, ...]
    modifier: int = 0

    def __post_init__(self) -> None:
        if len(self.terms) > MAX_TERMS:
            raise ValueError("Dice expression has too many terms")
        if sum(term.count for term in self.terms) > MAX_TOTAL_DICE:
            raise ValueError("Dice expression has too many dice")
        if type(self.modifier) is not int or abs(self.modifier) > MAX_ABS_MODIFIER:
            raise ValueError("Dice expression modifier exceeds safety limits")
        if not self.terms and self.modifier < 0:
            raise ValueError("Fixed dice value cannot be negative")

    @property
    def dice_count(self) -> int:
        return sum(term.count for term in self.terms)

    @property
    def normalized(self) -> str:
        value = "+".join(term.normalized for term in self.terms)
        if not value:
            return str(self.modifier)
        if self.modifier > 0:
            return f"{value}+{self.modifier}"
        if self.modifier < 0:
            return f"{value}{self.modifier}"
        return value

    def roll(self, randbelow: Callable[[int], int]) -> tuple[list[int], list[dict[str, object]]]:
        """Generate flat legacy rolls plus auditable evidence for every AST term."""

        flat: list[int] = []
        evidence: list[dict[str, object]] = []
        for index, term in enumerate(self.terms):
            rolls = [randbelow(term.sides) + 1 for _ in range(term.count)]
            flat.extend(rolls)
            evidence.append(
                {
                    "term_index": index,
                    "expression": term.normalized,
                    "rolls": rolls,
                    "subtotal": sum(rolls),
                }
            )
        return flat, evidence

    def evaluate(self, rolls: Sequence[int]) -> tuple[int, tuple[dict[str, object], ...]]:
        """Consume recorded faces in AST order and return total plus term evidence."""

        if len(rolls) != self.dice_count:
            raise ValueError(
                f"Expected {self.dice_count} recorded dice, received {len(rolls)}"
            )
        offset = 0
        evidence: list[dict[str, object]] = []
        for index, term in enumerate(self.terms):
            term_rolls = list(rolls[offset : offset + term.count])
            if any(type(roll) is not int or not 1 <= roll <= term.sides for roll in term_rolls):
                raise ValueError(
                    f"Every recorded die for term {index} must be between 1 and {term.sides}"
                )
            offset += term.count
            evidence.append(
                {
                    "term_index": index,
                    "expression": term.normalized,
                    "rolls": term_rolls,
                    "subtotal": sum(term_rolls),
                }
            )
        return sum(int(item["subtotal"]) for item in evidence) + self.modifier, tuple(evidence)


def parse_dice_expression(expression: str) -> AdditiveDiceExpression:
    """Parse a small additive grammar and fail closed on names or punctuation."""

    if not isinstance(expression, str):
        raise TypeError("Dice expression must be text")
    compact = re.sub(r"\s+", "", expression)
    if not compact:
        raise ValueError("Dice expression cannot be empty")
    if len(compact) > 256:
        raise ValueError("Dice expression exceeds safety limits")

    terms: list[DiceTerm] = []
    modifier = 0
    position = 0
    first = True
    syntactic_terms = 0
    while position < len(compact):
        sign = 1
        if not first:
            operator = compact[position : position + 1]
            if operator not in {"+", "-"}:
                raise ValueError("Dice expression only accepts + or - between terms")
            sign = -1 if operator == "-" else 1
            position += 1
        match = _TERM.match(compact, position)
        if match is None:
            raise ValueError("Dice expression must contain only dice and integer terms")
        position = match.end()
        first = False
        syntactic_terms += 1
        if match.group("integer") is not None:
            modifier += sign * int(match.group("integer"))
            continue
        if sign < 0:
            raise ValueError("Dice terms cannot be subtracted")
        terms.append(DiceTerm(int(match.group("count")), int(match.group("sides"))))

    if syntactic_terms > MAX_TERMS:
        raise ValueError("Dice expression has too many terms")
    return AdditiveDiceExpression(tuple(terms), modifier)


__all__ = [
    "MAX_ABS_MODIFIER",
    "MAX_SIDES",
    "MAX_TERMS",
    "MAX_TOTAL_DICE",
    "AdditiveDiceExpression",
    "DiceTerm",
    "parse_dice_expression",
]
