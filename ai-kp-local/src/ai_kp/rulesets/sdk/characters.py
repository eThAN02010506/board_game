"""Ruleset-neutral character validation contracts."""

from dataclasses import asdict, dataclass
from typing import Any, Literal

CharacterValidationLayer = Literal["structure", "ruleset", "review_policy"]
CharacterValidationSeverity = Literal["warning", "error"]


@dataclass(frozen=True)
class CharacterValidationIssue:
    layer: CharacterValidationLayer
    code: str
    message: str
    path: str = ""
    severity: CharacterValidationSeverity = "warning"
    requires_kp_review: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CharacterValidationReport:
    issues: tuple[CharacterValidationIssue, ...] = ()

    @property
    def warnings(self) -> list[str]:
        return list(dict.fromkeys(issue.message for issue in self.issues))

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def requires_kp_review(self) -> bool:
        return any(issue.requires_kp_review for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        counts = {
            layer: sum(issue.layer == layer for issue in self.issues)
            for layer in ("structure", "ruleset", "review_policy")
        }
        return {
            "issues": [issue.as_dict() for issue in self.issues],
            "counts": counts,
            "has_errors": self.has_errors,
            "requires_kp_review": self.requires_kp_review,
        }


@dataclass(frozen=True)
class CharacterSheetValidation:
    canonical_sheet: dict[str, Any]
    report: CharacterValidationReport

    def as_preview(self) -> dict[str, Any]:
        return {
            "canonical_sheet": self.canonical_sheet,
            "warnings": self.report.warnings,
            "validation": self.report.as_dict(),
        }


__all__ = [
    "CharacterSheetValidation",
    "CharacterValidationIssue",
    "CharacterValidationLayer",
    "CharacterValidationReport",
    "CharacterValidationSeverity",
]
