from dataclasses import dataclass, field
from typing import Any, Literal

from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.rulesets import DEFAULT_RULESET_ID, get_ruleset


@dataclass(frozen=True)
class CreateInvestigatorCommand:
    canonical_sheet: dict[str, Any]
    source_type: Literal["manual", "xlsx"] = "manual"
    source_hash: str | None = None
    source_filename: str | None = None
    template_id: str | None = None
    parser_version: str | None = None
    warnings: list[str] = field(default_factory=list)


class InvestigatorService:
    def __init__(self, repo: Repository, ruleset_id: str = DEFAULT_RULESET_ID):
        self.repo = repo
        self.ruleset = get_ruleset(ruleset_id)

    def create_profile(self, display_name: str) -> dict:
        name = display_name.strip()
        if not name:
            raise ValueError("Player display name is required")
        return self.repo.create_player_profile(name)

    def preview_excel(self, data: bytes, filename: str) -> dict:
        return self.ruleset.import_character_xlsx(data, filename)

    def preview_manual(self, canonical_sheet: dict[str, Any]) -> dict:
        ruleset = get_ruleset(
            canonical_sheet.get("ruleset_id") or self.ruleset.manifest.ruleset_id
        )
        canonical, warnings = ruleset.normalize_character_sheet(canonical_sheet)
        return {"canonical_sheet": canonical, "warnings": warnings}

    def create_investigator(
        self,
        owner_profile_id: str,
        command: CreateInvestigatorCommand,
    ) -> dict:
        ruleset = get_ruleset(
            command.canonical_sheet.get("ruleset_id") or self.ruleset.manifest.ruleset_id
        )
        canonical, rule_warnings = ruleset.normalize_character_sheet(
            command.canonical_sheet
        )
        warnings = list(dict.fromkeys([*command.warnings, *rule_warnings]))
        return self.repo.create_investigator(
            owner_profile_id,
            canonical,
            source_type=command.source_type,
            source_hash=command.source_hash,
            source_filename=command.source_filename,
            template_id=command.template_id,
            parser_version=command.parser_version,
            warnings=warnings,
        )

    def list_investigators(self, owner_profile_id: str) -> list[dict]:
        return self.repo.list_investigators(owner_profile_id)

    def get_investigator(self, owner_profile_id: str, investigator_id: str) -> dict:
        return self.repo.get_investigator(investigator_id, owner_profile_id)

    def revise_investigator(
        self,
        owner_profile_id: str,
        investigator_id: str,
        command: CreateInvestigatorCommand,
    ) -> dict:
        ruleset = get_ruleset(
            command.canonical_sheet.get("ruleset_id") or self.ruleset.manifest.ruleset_id
        )
        canonical, rule_warnings = ruleset.normalize_character_sheet(
            command.canonical_sheet
        )
        warnings = list(dict.fromkeys([*command.warnings, *rule_warnings]))
        return self.repo.add_investigator_revision(
            investigator_id,
            owner_profile_id,
            canonical,
            source_type=command.source_type,
            source_hash=command.source_hash,
            source_filename=command.source_filename,
            template_id=command.template_id,
            parser_version=command.parser_version,
            warnings=warnings,
        )

    def list_revisions(self, owner_profile_id: str, investigator_id: str) -> list[dict]:
        return self.repo.list_investigator_revisions(investigator_id, owner_profile_id)

    def submit_to_campaign(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        revision_id: str | None,
        owner_profile_id: str,
        member_id: str,
        session_id: str,
    ) -> dict:
        investigator = self.repo.get_investigator(investigator_id, owner_profile_id)
        selected_revision_id = revision_id or str(investigator["current_revision_id"])
        result = self.repo.submit_investigator_to_campaign(
            campaign_id=campaign_id,
            investigator_id=investigator_id,
            revision_id=selected_revision_id,
            owner_profile_id=owner_profile_id,
            member_id=member_id,
            session_id=session_id,
        )
        return self._with_diff(result)

    def list_player_campaign_investigators(
        self, campaign_id: str, owner_profile_id: str
    ) -> list[dict]:
        return [
            self._with_diff(item)
            for item in self.repo.list_campaign_investigators(
                campaign_id, owner_profile_id
            )
        ]

    def list_kp_campaign_investigators(self, campaign_id: str) -> list[dict]:
        return [
            self._with_diff(item)
            for item in self.repo.list_campaign_investigators(campaign_id)
        ]

    def review(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        action: str,
        comment: str | None,
        kp_member_id: str,
        session_id: str,
    ) -> dict:
        result = self.repo.review_campaign_investigator(
            campaign_id=campaign_id,
            investigator_id=investigator_id,
            action=action,
            comment=comment,
            kp_member_id=kp_member_id,
            session_id=session_id,
        )
        return self._with_diff(result)

    def assign(
        self, *, session_id: str, member_id: str, investigator_id: str
    ) -> dict:
        return self.repo.assign_approved_investigator(
            session_id=session_id,
            member_id=member_id,
            investigator_id=investigator_id,
        )

    def list_public(self, campaign_id: str) -> list[dict]:
        return self.repo.list_public_campaign_investigators(campaign_id)

    def update_campaign_state(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        expected_version: int,
        changes: dict[str, Any],
    ) -> dict:
        return self.repo.update_investigator_campaign_state(
            campaign_id=campaign_id,
            investigator_id=investigator_id,
            expected_version=expected_version,
            changes=changes,
        )

    @staticmethod
    def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
        if isinstance(value, dict):
            flattened: dict[str, Any] = {}
            for key in sorted(value):
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                flattened.update(InvestigatorService._flatten(value[key], child_prefix))
            return flattened
        if isinstance(value, list):
            return {prefix: value}
        return {prefix: value}

    @classmethod
    def _with_diff(cls, result: dict) -> dict:
        submitted = result.get("submitted_revision")
        approved = result.get("approved_revision")
        before = cls._flatten((approved or {}).get("canonical_sheet") or {})
        after = cls._flatten((submitted or {}).get("canonical_sheet") or {})
        result["diff"] = [
            {"path": path, "before": before.get(path), "after": after.get(path)}
            for path in sorted(set(before) | set(after))
            if before.get(path) != after.get(path)
        ]
        return result
