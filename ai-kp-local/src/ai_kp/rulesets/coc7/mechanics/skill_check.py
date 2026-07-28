"""Canonical pure and replayable CoC7 percentile-check implementation."""

import random
import secrets
from dataclasses import dataclass
from typing import Literal

SuccessLevel = Literal["fumble", "failure", "regular", "hard", "extreme", "critical"]
Difficulty = Literal["regular", "hard", "extreme"]

RULESET_ID = "coc7-keeper-cn-2002c"
RULESET_VERSION = "2002c"
SOURCE_REFERENCE = {
    "source_id": RULESET_ID,
    "chapter": "第五章 游戏系统",
    "page_start": 77,
    "page_end": 78,
    "sections": ["5.7 对抗检定", "5.8 奖励骰与惩罚骰"],
}

_SUCCESS_RANK: dict[SuccessLevel, int] = {
    "fumble": -1,
    "failure": 0,
    "regular": 1,
    "hard": 2,
    "extreme": 3,
    "critical": 4,
}
_DIFFICULTY_RANK: dict[Difficulty, int] = {"regular": 1, "hard": 2, "extreme": 3}


@dataclass(frozen=True)
class D100Check:
    skill: str
    target: int
    roll: int

    @property
    def success_level(self) -> SuccessLevel:
        return success_level(self.target, self.roll)


@dataclass(frozen=True)
class D100Resolution:
    target: int
    difficulty: Difficulty
    bonus_dice: int
    ones_digit: int
    tens_digits: tuple[int, ...]
    candidates: tuple[int, ...]
    selected_roll: int
    threshold: int
    success_level: SuccessLevel
    passed: bool

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "difficulty": self.difficulty,
            "bonus_dice": self.bonus_dice,
            "raw_dice": {
                "ones_digit": self.ones_digit,
                "tens_digits": list(self.tens_digits),
                "candidates": list(self.candidates),
            },
            "selected_roll": self.selected_roll,
            "threshold": self.threshold,
            "success_level": self.success_level,
            "passed": self.passed,
            "ruleset_id": RULESET_ID,
            "ruleset_version": RULESET_VERSION,
            "source_reference": SOURCE_REFERENCE,
        }


def success_level(target: int, roll: int) -> SuccessLevel:
    _validate_target(target)
    if not 1 <= roll <= 100:
        raise ValueError("D100 roll must be between 1 and 100")
    if roll == 1:
        return "critical"
    if roll == 100 or (target < 50 and roll >= 96):
        return "fumble"
    if roll <= target // 5:
        return "extreme"
    if roll <= target // 2:
        return "hard"
    if roll <= target:
        return "regular"
    return "failure"


def threshold_for(target: int, difficulty: Difficulty) -> int:
    _validate_target(target)
    if difficulty == "regular":
        return target
    if difficulty == "hard":
        return target // 2
    if difficulty == "extreme":
        return target // 5
    raise ValueError(f"Unsupported difficulty: {difficulty}")


def resolve_d100(
    *,
    target: int,
    difficulty: Difficulty,
    bonus_dice: int,
    ones_digit: int,
    tens_digits: tuple[int, ...] | list[int],
) -> D100Resolution:
    _validate_target(target)
    if bonus_dice < -2 or bonus_dice > 2:
        raise ValueError("Bonus dice must be between -2 and 2")
    expected_tens = abs(bonus_dice) + 1
    normalized_tens = tuple(int(value) for value in tens_digits)
    if len(normalized_tens) != expected_tens:
        raise ValueError(f"Expected {expected_tens} tens dice, received {len(normalized_tens)}")
    if not 0 <= ones_digit <= 9 or any(not 0 <= value <= 9 for value in normalized_tens):
        raise ValueError("Ones and tens dice must be digits between 0 and 9")
    candidates = tuple(_combine_percentile(value, ones_digit) for value in normalized_tens)
    selected = min(candidates) if bonus_dice > 0 else max(candidates) if bonus_dice < 0 else candidates[0]
    level = success_level(target, selected)
    return D100Resolution(
        target=target,
        difficulty=difficulty,
        bonus_dice=bonus_dice,
        ones_digit=ones_digit,
        tens_digits=normalized_tens,
        candidates=candidates,
        selected_roll=selected,
        threshold=threshold_for(target, difficulty),
        success_level=level,
        passed=_SUCCESS_RANK[level] >= _DIFFICULTY_RANK[difficulty],
    )


def secure_d100_dice(bonus_dice: int) -> tuple[int, tuple[int, ...]]:
    if bonus_dice < -2 or bonus_dice > 2:
        raise ValueError("Bonus dice must be between -2 and 2")
    return secrets.randbelow(10), tuple(
        secrets.randbelow(10) for _ in range(abs(bonus_dice) + 1)
    )


def roll_d100_check(skill: str, target: int, rng: random.Random | None = None) -> D100Check:
    roller = rng or random.SystemRandom()
    return D100Check(skill=skill, target=target, roll=roller.randint(1, 100))


def _combine_percentile(tens_digit: int, ones_digit: int) -> int:
    value = tens_digit * 10 + ones_digit
    return 100 if value == 0 else value


def _validate_target(target: int) -> None:
    if not 0 <= target <= 100:
        raise ValueError("D100 target must be between 0 and 100")


__all__ = [
    "RULESET_ID",
    "RULESET_VERSION",
    "SOURCE_REFERENCE",
    "D100Check",
    "D100Resolution",
    "Difficulty",
    "resolve_d100",
    "roll_d100_check",
    "secure_d100_dice",
    "success_level",
    "threshold_for",
]
