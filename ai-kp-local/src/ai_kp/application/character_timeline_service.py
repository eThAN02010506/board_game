"""Application policy for cross-campaign investigator continuity."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ai_kp.application.ports.repositories import CharacterTimelineStore
from ai_kp.rulesets import get_ruleset

TEXT_CHANGE_TARGETS = {
    "major_experience": ("traits", "重要经历"),
    "scar": ("injuries_scars", "永久伤痕"),
    "relationship": ("significant_people", "重要关系"),
    "spell": ("secret", "法术"),
}
CHARACTERISTIC_KEYS = {
    "str",
    "con",
    "siz",
    "dex",
    "app",
    "int",
    "pow",
    "edu",
    "luck",
}


@dataclass(frozen=True)
class PermanentChangeCommand:
    kind: str
    summary: str
    change: dict[str, Any]
    source_event_id: str
    rationale: str


@dataclass(frozen=True)
class PermanentChangeDecisionCommand:
    action: str
    reason: str
    expected_revision_id: str


class CharacterTimelineService:
    def __init__(self, repo: CharacterTimelineStore):
        self.repo = repo

    def create_branch(
        self,
        investigator_id: str,
        owner_profile_id: str,
        label: str,
    ) -> dict:
        return self.repo.create_timeline_branch(
            investigator_id,
            owner_profile_id,
            label,
        )

    def owner_timeline(
        self,
        investigator_id: str,
        owner_profile_id: str,
    ) -> dict:
        self.repo.get_investigator(investigator_id, owner_profile_id)
        return self.repo.get_character_timeline(investigator_id)

    def keeper_timeline(
        self,
        campaign_id: str,
        investigator_id: str,
    ) -> dict:
        record = self.repo.get_campaign_investigator(campaign_id, investigator_id)
        if record["status"] != "approved" or not record.get("approved_revision_id"):
            raise KeyError(f"Approved campaign investigator not found: {investigator_id}")
        return self.repo.get_character_timeline(investigator_id)

    def propose(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        member_id: str,
        command: PermanentChangeCommand,
    ) -> dict:
        self._validate_change(command.kind, command.change)
        if not command.summary.strip():
            raise ValueError("Permanent change summary is required")
        if not command.rationale.strip():
            raise ValueError("Permanent change rationale is required")
        return self.repo.create_permanent_change_proposal(
            campaign_id=campaign_id,
            investigator_id=investigator_id,
            kind=command.kind,
            summary=command.summary,
            change=command.change,
            source_event_id=command.source_event_id,
            rationale=command.rationale,
            proposed_by_member_id=member_id,
        )

    def list_proposals(
        self,
        investigator_id: str,
        owner_profile_id: str,
    ) -> list[dict]:
        return self.repo.list_permanent_change_proposals(
            investigator_id,
            owner_profile_id,
        )

    def decide(
        self,
        proposal_id: str,
        owner_profile_id: str,
        command: PermanentChangeDecisionCommand,
    ) -> dict:
        if command.action not in {"accepted", "rejected"}:
            raise ValueError("Permanent change decision must be accepted or rejected")
        reason = command.reason.strip()
        if not reason:
            raise ValueError("Permanent change decision reason is required")
        proposal = self.repo.get_permanent_change_proposal(proposal_id)
        if proposal["owner_profile_id"] != owner_profile_id:
            raise KeyError(f"Permanent change proposal not found: {proposal_id}")
        canonical_sheet = None
        warnings: list[str] = []
        if command.action == "accepted":
            base = self.repo.get_investigator_revision(
                str(proposal["base_revision_id"])
            )
            canonical_sheet = self._apply_change(
                base["canonical_sheet"],
                str(proposal["kind"]),
                dict(proposal["change"]),
            )
            validation = get_ruleset(
                canonical_sheet.get("ruleset_id") or "coc7-keeper-cn-2002c"
            ).validate_character_sheet(canonical_sheet)
            if validation.report.has_errors:
                raise ValueError("Permanent change produces an invalid character sheet")
            canonical_sheet = validation.canonical_sheet
            warnings = validation.report.warnings
        return self.repo.decide_permanent_change(
            proposal_id,
            owner_profile_id=owner_profile_id,
            action=command.action,
            reason=reason,
            expected_revision_id=command.expected_revision_id,
            canonical_sheet=canonical_sheet,
            warnings=warnings,
        )

    @staticmethod
    def _validate_change(kind: str, change: dict[str, Any]) -> None:
        if kind in TEXT_CHANGE_TARGETS:
            if set(change) != {"text"}:
                raise ValueError(f"{kind} change requires only text")
            text = str(change.get("text") or "").strip()
            if not 1 <= len(text) <= 1000:
                raise ValueError("Permanent change text must contain 1-1000 characters")
            return
        if kind == "characteristic":
            if set(change) != {"key", "new_value"}:
                raise ValueError("Characteristic change requires key and new_value")
            if change.get("key") not in CHARACTERISTIC_KEYS:
                raise ValueError("Unknown investigator characteristic")
            value = change.get("new_value")
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 99:
                raise ValueError("Characteristic value must be between 1 and 99")
            return
        if kind == "skill":
            if not {"skill_key", "new_value"} <= set(change) or set(change) - {
                "skill_key",
                "specialization",
                "new_value",
            }:
                raise ValueError(
                    "Skill change requires skill_key, new_value and optional specialization"
                )
            if not str(change.get("skill_key") or "").strip():
                raise ValueError("Skill change requires a skill_key")
            value = change.get("new_value")
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 200:
                raise ValueError("Skill value must be between 0 and 200")
            return
        raise ValueError("Unsupported permanent change kind")

    @classmethod
    def _apply_change(
        cls,
        sheet: dict[str, Any],
        kind: str,
        change: dict[str, Any],
    ) -> dict[str, Any]:
        cls._validate_change(kind, change)
        result = deepcopy(sheet)
        if kind in TEXT_CHANGE_TARGETS:
            field, label = TEXT_CHANGE_TARGETS[kind]
            background = result.setdefault("background", {})
            existing = str(background.get(field) or "").strip()
            line = f"[{label}] {str(change['text']).strip()}"
            if line not in existing.splitlines():
                background[field] = "\n".join(part for part in (existing, line) if part)
            return result
        if kind == "characteristic":
            result.setdefault("characteristics", {})[str(change["key"])] = int(
                change["new_value"]
            )
            return result
        skills = result.get("skills") or []
        specialization = str(change.get("specialization") or "").strip()
        matches = [
            skill
            for skill in skills
            if isinstance(skill, dict)
            and skill.get("skill_key") == change["skill_key"]
            and (
                not specialization
                or str(skill.get("specialization") or "").strip() == specialization
            )
        ]
        if len(matches) != 1:
            raise ValueError("Skill change must match exactly one existing skill")
        skill = matches[0]
        fixed = sum(
            max(0, int(skill.get(key) or 0))
            for key in ("base_value", "occupation_points", "interest_points")
        )
        new_value = int(change["new_value"])
        if new_value < fixed:
            raise ValueError("Skill milestone cannot reduce creation-point components")
        skill["development_points"] = new_value - fixed
        skill["growth_mark"] = False
        return result


__all__ = [
    "CharacterTimelineService",
    "PermanentChangeCommand",
    "PermanentChangeDecisionCommand",
]
