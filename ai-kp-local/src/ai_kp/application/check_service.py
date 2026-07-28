from dataclasses import dataclass

from ai_kp.application.ports.repositories import CheckStore
from ai_kp.platform.sessions.models import AuthenticatedMember
from ai_kp.rulesets import get_ruleset


@dataclass(frozen=True)
class CreateCheckCommand:
    skill_name: str
    difficulty: str = "regular"
    bonus_dice: int = 0
    hidden: bool = False
    allow_push: bool = True
    roller_member_id: str | None = None
    pc_id: str | None = None
    target: int | None = None
    proposal_id: str | None = None
    player_action_id: str | None = None


@dataclass(frozen=True)
class ResolveCheckCommand:
    input_method: str = "digital"
    ones_digit: int | None = None
    tens_digits: tuple[int, ...] = ()


class CheckService:
    """Authorize checks while delegating mechanics to the campaign ruleset."""

    def __init__(self, repo: CheckStore):
        self.repo = repo

    def create(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: CreateCheckCommand,
    ) -> dict:
        self._require_kp(identity, campaign_id)
        campaign = self.repo.get_campaign(campaign_id)
        ruleset = get_ruleset(str(campaign["system"]))
        ruleset.validate_check(
            target=command.target,
            difficulty=command.difficulty,
            bonus_dice=command.bonus_dice,
        )
        manifest = ruleset.manifest
        return self.repo.create_skill_check(
            campaign_id=campaign_id,
            session_id=identity.session_id,
            requested_by_member_id=identity.member_id,
            skill_name=command.skill_name,
            difficulty=command.difficulty,
            ruleset_id=manifest.ruleset_id,
            ruleset_version=manifest.version,
            source_reference=dict(manifest.source_reference),
            bonus_dice=command.bonus_dice,
            hidden=command.hidden,
            allow_push=command.allow_push,
            roller_member_id=command.roller_member_id,
            pc_id=command.pc_id,
            target=command.target,
            proposal_id=command.proposal_id,
            player_action_id=command.player_action_id,
        )

    def list(self, campaign_id: str, identity: AuthenticatedMember) -> list[dict]:
        self._require_campaign(identity, campaign_id)
        checks = self.repo.list_skill_checks(campaign_id, identity.session_id)
        if identity.role == "kp":
            return checks
        return [
            check
            for check in checks
            if not check["hidden"] and check["roller_member_id"] == identity.member_id
        ]

    def get(self, check_id: str, identity: AuthenticatedMember) -> dict:
        check = self.repo.get_skill_check(check_id)
        self._require_visible(check, identity)
        return check

    def resolve(
        self,
        check_id: str,
        identity: AuthenticatedMember,
        command: ResolveCheckCommand,
    ) -> dict:
        check = self.repo.get_skill_check(check_id)
        self._require_visible(check, identity, resolving=True)
        ruleset = get_ruleset(str(check["ruleset_id"]))
        if command.input_method == "digital":
            raw_dice = ruleset.generate_check_dice(int(check["bonus_dice"]))
        elif command.input_method == "physical":
            if command.ones_digit is None:
                raise ValueError("Physical dice require the ones digit")
            raw_dice = {
                "ones_digit": command.ones_digit,
                "tens_digits": list(command.tens_digits),
            }
        else:
            raise ValueError("Input method must be digital or physical")
        resolution = ruleset.resolve_check(
            target=int(check["target"]),
            difficulty=check["difficulty"],
            bonus_dice=int(check["bonus_dice"]),
            raw_dice=raw_dice,
        )
        return self.repo.resolve_skill_check(
            check_id,
            actor_member_id=identity.member_id,
            input_method=command.input_method,
            resolution=resolution,
        )

    def replay(self, check_id: str, identity: AuthenticatedMember) -> dict:
        check = self.get(check_id, identity)
        if check["status"] not in {"resolved", "overridden"} or not check["raw_dice"]:
            raise ValueError("Only a resolved check can be replayed")
        ruleset = get_ruleset(str(check["ruleset_id"]))
        replayed = ruleset.resolve_check(
            target=int(check["target"]),
            difficulty=check["difficulty"],
            bonus_dice=int(check["bonus_dice"]),
            raw_dice=check["raw_dice"],
        )
        recorded = check.get("original_result") if check["status"] == "overridden" else check
        matches = all(
            replayed[key] == recorded[key]
            for key in ("selected_roll", "threshold", "success_level", "passed")
        )
        return {
            "check_id": check_id,
            "matches_recorded_result": matches,
            "replayed": replayed,
            "recorded_status": check["status"],
            "override_reason": check.get("override_reason"),
        }

    def override(
        self,
        check_id: str,
        identity: AuthenticatedMember,
        *,
        success_level: str,
        passed: bool,
        reason: str,
    ) -> dict:
        check = self.repo.get_skill_check(check_id)
        self._require_kp_check(identity, check)
        return self.repo.override_skill_check(
            check_id,
            actor_member_id=identity.member_id,
            success_level=success_level,
            passed=passed,
            reason=reason,
        )

    def cancel(
        self, check_id: str, identity: AuthenticatedMember, *, reason: str
    ) -> dict:
        check = self.repo.get_skill_check(check_id)
        self._require_kp_check(identity, check)
        return self.repo.cancel_skill_check(
            check_id, actor_member_id=identity.member_id, reason=reason
        )

    def push(self, check_id: str, identity: AuthenticatedMember, *, reason: str) -> dict:
        check = self.repo.get_skill_check(check_id)
        self._require_kp_check(identity, check)
        return self.repo.push_skill_check(
            check_id, actor_member_id=identity.member_id, reason=reason
        )

    @staticmethod
    def _require_campaign(identity: AuthenticatedMember, campaign_id: str) -> None:
        if identity.campaign_id != campaign_id:
            raise PermissionError("Session token belongs to another campaign")

    @classmethod
    def _require_kp(cls, identity: AuthenticatedMember, campaign_id: str) -> None:
        cls._require_campaign(identity, campaign_id)
        if identity.role != "kp":
            raise PermissionError("KP access required")

    @classmethod
    def _require_kp_check(
        cls,
        identity: AuthenticatedMember,
        check: dict,
    ) -> None:
        cls._require_kp(identity, str(check["campaign_id"]))
        if identity.session_id != check["session_id"]:
            raise PermissionError("Check belongs to another session")

    @classmethod
    def _require_visible(
        cls,
        check: dict,
        identity: AuthenticatedMember,
        *,
        resolving: bool = False,
    ) -> None:
        cls._require_campaign(identity, str(check["campaign_id"]))
        if identity.session_id != check["session_id"]:
            raise PermissionError("Check belongs to another session")
        if identity.role == "kp":
            return
        if check["hidden"]:
            raise PermissionError("Hidden checks are visible only to KP")
        if check["roller_member_id"] != identity.member_id:
            raise PermissionError("Players can only access their own checks")
        if resolving and check["status"] != "requested":
            raise ValueError("Only a requested check can be resolved")


__all__ = ["CheckService", "CreateCheckCommand", "ResolveCheckCommand"]
