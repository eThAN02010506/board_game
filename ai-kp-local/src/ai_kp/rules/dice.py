import random
from dataclasses import dataclass


@dataclass(frozen=True)
class D100Check:
    skill: str
    target: int
    roll: int

    @property
    def success_level(self) -> str:
        if self.roll == 1:
            return "critical"
        if self.roll <= self.target // 5:
            return "extreme"
        if self.roll <= self.target // 2:
            return "hard"
        if self.roll <= self.target:
            return "regular"
        if self.roll >= 96 and self.target < 50:
            return "fumble"
        if self.roll == 100:
            return "fumble"
        return "failure"


def roll_d100_check(skill: str, target: int, rng: random.Random | None = None) -> D100Check:
    roller = rng or random.Random()
    return D100Check(skill=skill, target=target, roll=roller.randint(1, 100))

