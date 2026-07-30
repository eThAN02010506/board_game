"""Generate immutable dice evidence without interpreting game-system meaning."""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

DICE_ROLL_SCHEMA_VERSION = "dice-roll.v1"


@dataclass(frozen=True)
class DiceComponent:
    """One homogeneous group of independently generated integer faces."""

    key: str
    count: int
    sides: int
    minimum: int = 1

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("Dice component key is required")
        if self.count < 1 or self.count > 100:
            raise ValueError("Dice component count must be between 1 and 100")
        if self.sides < 2 or self.sides > 1_000_000:
            raise ValueError("Dice component sides must be between 2 and 1000000")

    @property
    def maximum(self) -> int:
        return self.minimum + self.sides - 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "count": self.count,
            "sides": self.sides,
            "minimum": self.minimum,
        }


@dataclass(frozen=True)
class DiceRollRequest:
    """A ruleset-provided request that the platform can execute generically."""

    components: tuple[DiceComponent, ...]
    schema_version: str = DICE_ROLL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DICE_ROLL_SCHEMA_VERSION:
            raise ValueError(f"Unsupported dice roll schema: {self.schema_version}")
        if not self.components:
            raise ValueError("At least one dice component is required")
        keys = [component.key for component in self.components]
        if len(keys) != len(set(keys)):
            raise ValueError("Dice component keys must be unique")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "components": [component.as_dict() for component in self.components],
        }


@dataclass(frozen=True)
class DiceRollResult:
    """Stored random facts plus a deterministic integrity fingerprint."""

    request: DiceRollRequest
    rolls: Mapping[str, tuple[int, ...]]
    evidence_fingerprint: str

    @classmethod
    def create(
        cls,
        request: DiceRollRequest,
        rolls: Mapping[str, tuple[int, ...] | list[int]],
    ) -> DiceRollResult:
        expected_keys = {component.key for component in request.components}
        if set(rolls) != expected_keys:
            raise ValueError("Dice roll keys do not match the request")
        normalized: dict[str, tuple[int, ...]] = {}
        for component in request.components:
            source_values = rolls[component.key]
            if any(type(value) is not int for value in source_values):
                raise TypeError(f"Dice component {component.key} values must be integers")
            values = tuple(source_values)
            if len(values) != component.count:
                raise ValueError(
                    f"Dice component {component.key} expected {component.count} values"
                )
            if any(
                value < component.minimum or value > component.maximum
                for value in values
            ):
                raise ValueError(
                    f"Dice component {component.key} values must be between "
                    f"{component.minimum} and {component.maximum}"
                )
            normalized[component.key] = values
        payload = {
            "request": request.as_dict(),
            "rolls": {key: list(values) for key, values in sorted(normalized.items())},
        }
        fingerprint = sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return cls(
            request=request,
            rolls=normalized,
            evidence_fingerprint=fingerprint,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DiceRollResult:
        if payload.get("schema_version") != DICE_ROLL_SCHEMA_VERSION:
            raise ValueError("Unsupported stored dice roll schema")
        raw_components = payload.get("components")
        raw_rolls = payload.get("rolls")
        if not isinstance(raw_components, list) or not isinstance(raw_rolls, Mapping):
            raise TypeError("Stored dice roll evidence is malformed")
        recorded = payload.get("evidence_fingerprint")
        if (
            not isinstance(recorded, str)
            or len(recorded) != 64
            or any(character not in "0123456789abcdef" for character in recorded)
        ):
            raise ValueError("Stored dice roll evidence fingerprint is required")
        request = DiceRollRequest(
            components=tuple(
                DiceComponent(
                    key=str(component["key"]),
                    count=int(component["count"]),
                    sides=int(component["sides"]),
                    minimum=int(component.get("minimum", 1)),
                )
                for component in raw_components
                if isinstance(component, Mapping)
            )
        )
        result = cls.create(
            request,
            {
                str(key): tuple(values)
                for key, values in raw_rolls.items()
                if isinstance(values, list)
            },
        )
        if recorded != result.evidence_fingerprint:
            raise ValueError("Stored dice roll evidence fingerprint does not match")
        return result

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.request.as_dict(),
            "rolls": {
                key: list(values) for key, values in sorted(self.rolls.items())
            },
            "evidence_fingerprint": self.evidence_fingerprint,
        }


class SecureDiceRoller:
    """Use the operating system CSPRNG and return replayable face evidence."""

    def roll(self, request: DiceRollRequest) -> DiceRollResult:
        return DiceRollResult.create(
            request,
            {
                component.key: tuple(
                    secrets.randbelow(component.sides) + component.minimum
                    for _ in range(component.count)
                )
                for component in request.components
            },
        )


__all__ = [
    "DICE_ROLL_SCHEMA_VERSION",
    "DiceComponent",
    "DiceRollRequest",
    "DiceRollResult",
    "SecureDiceRoller",
]
