from __future__ import annotations

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


@dataclass(frozen=True)
class OpposedSideCommand:
    skill_name: str
    target: int | None = None
    bonus_dice: int = 0
    hidden: bool = False
    roller_member_id: str | None = None
    pc_id: str | None = None


@dataclass(frozen=True)
class CreateOpposedCheckCommand:
    left: OpposedSideCommand
    right: OpposedSideCommand
    proposal_id: str | None = None
    player_action_id: str | None = None


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

    def create_opposed(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: CreateOpposedCheckCommand,
    ) -> dict:
        self._require_kp(identity, campaign_id)
        self.repo.begin_immediate()
        created: list[dict] = []
        for side in (command.left, command.right):
            created.append(
                self.create(
                    campaign_id,
                    identity,
                    CreateCheckCommand(
                        skill_name=side.skill_name,
                        difficulty="regular",
                        bonus_dice=side.bonus_dice,
                        hidden=side.hidden,
                        allow_push=False,
                        roller_member_id=side.roller_member_id,
                        pc_id=side.pc_id,
                        target=side.target,
                        proposal_id=command.proposal_id,
                        player_action_id=command.player_action_id,
                    ),
                )
            )
        return self.repo.create_opposed_check(
            campaign_id=campaign_id,
            session_id=identity.session_id,
            requested_by_member_id=identity.member_id,
            left_check_id=str(created[0]["id"]),
            right_check_id=str(created[1]["id"]),
        )

    def list_opposed(
        self, campaign_id: str, identity: AuthenticatedMember
    ) -> list[dict]:
        self._require_campaign(identity, campaign_id)
        contests = self.repo.list_opposed_checks(campaign_id, identity.session_id)
        if identity.role == "kp":
            return contests
        return [
            contest
            for contest in contests
            if not contest["left_check"]["hidden"]
            and not contest["right_check"]["hidden"]
            and identity.member_id
            in {
                contest["left_check"]["roller_member_id"],
                contest["right_check"]["roller_member_id"],
            }
        ]

    def resolve_opposed(
        self, opposed_check_id: str, identity: AuthenticatedMember
    ) -> dict:
        opposed = self.repo.get_opposed_check(opposed_check_id)
        self._require_kp(identity, str(opposed["campaign_id"]))
        if identity.session_id != opposed["session_id"]:
            raise PermissionError("Opposed check belongs to another session")
        left, right = opposed["left_check"], opposed["right_check"]
        if any(check["status"] not in {"resolved", "overridden"} for check in (left, right)):
            raise ValueError("Both sides must have terminal rolled results")
        if left["ruleset_id"] != right["ruleset_id"]:
            raise ValueError("Opposed check rulesets no longer match")
        ruleset = get_ruleset(str(left["ruleset_id"]))
        result = ruleset.resolve_opposed_check(
            left_participant_id=str(left["id"]),
            left_target=int(left["target"]),
            left_roll=int(left["selected_roll"]),
            right_participant_id=str(right["id"]),
            right_target=int(right["target"]),
            right_roll=int(right["selected_roll"]),
        )
        # KP overrides are authoritative inputs, never silently discarded.
        if left["status"] == "overridden" or right["status"] == "overridden":
            from ai_kp.rulesets.coc7.mechanics.opposed_check import (
                OpposedParticipant,
                resolve_opposed,
            )

            result = resolve_opposed(
                OpposedParticipant(
                    str(left["id"]), int(left["target"]), int(left["selected_roll"]),
                    left["success_level"],
                ),
                OpposedParticipant(
                    str(right["id"]), int(right["target"]), int(right["selected_roll"]),
                    right["success_level"],
                ),
            ).as_dict()
        result["participant_labels"] = {
            str(left["id"]): left["skill_name"],
            str(right["id"]): right["skill_name"],
        }
        return self.repo.resolve_opposed_check(
            opposed_check_id,
            actor_member_id=identity.member_id,
            result=result,
        )

    def reroll_opposed(
        self, opposed_check_id: str, identity: AuthenticatedMember
    ) -> dict:
        opposed = self.repo.get_opposed_check(opposed_check_id)
        self._require_kp(identity, str(opposed["campaign_id"]))
        if identity.session_id != opposed["session_id"]:
            raise PermissionError("Opposed check belongs to another session")
        if opposed["status"] != "reroll_required":
            raise ValueError("Only an exact tied opposed check can be rerolled")
        existing = next(
            (
                item
                for item in self.repo.list_opposed_checks(
                    str(opposed["campaign_id"]), identity.session_id
                )
                if item.get("rerolled_from_opposed_check_id") == opposed_check_id
            ),
            None,
        )
        if existing is not None:
            return existing
        left, right = opposed["left_check"], opposed["right_check"]
        self.repo.begin_immediate()
        children = []
        for check in (left, right):
            children.append(
                self.create(
                    str(opposed["campaign_id"]),
                    identity,
                    CreateCheckCommand(
                        skill_name=str(check["skill_name"]),
                        difficulty="regular",
                        bonus_dice=int(check["bonus_dice"]),
                        hidden=bool(check["hidden"]),
                        allow_push=False,
                        roller_member_id=check["roller_member_id"],
                        pc_id=check["pc_id"],
                        target=int(check["target"]),
                        proposal_id=check["proposal_id"],
                        player_action_id=check["player_action_id"],
                    ),
                )
            )
        return self.repo.create_opposed_check(
            campaign_id=str(opposed["campaign_id"]),
            session_id=identity.session_id,
            requested_by_member_id=identity.member_id,
            left_check_id=str(children[0]["id"]),
            right_check_id=str(children[1]["id"]),
            rerolled_from_opposed_check_id=opposed_check_id,
        )

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


__all__ = [
    "CheckService",
    "CreateCheckCommand",
    "CreateOpposedCheckCommand",
    "OpposedSideCommand",
    "ResolveCheckCommand",
]
