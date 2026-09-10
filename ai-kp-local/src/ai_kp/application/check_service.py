from __future__ import annotations

from dataclasses import dataclass

from ai_kp.application.ports.repositories import CheckStore
from ai_kp.application.skill_growth_service import SkillGrowthService
from ai_kp.platform.randomness import DiceRollResult, SecureDiceRoller
from ai_kp.platform.sessions.models import AuthenticatedMember
from ai_kp.rulesets import get_campaign_ruleset, get_ruleset


@dataclass(frozen=True)
class CreateCheckCommand:
    skill_name: str
    difficulty: str = "regular"
    bonus_dice: int = 0
    hidden: bool = False
    visibility: str | None = None
    allow_push: bool = True
    roller_member_id: str | None = None
    pc_id: str | None = None
    target: int | None = None
    proposal_id: str | None = None
    player_action_id: str | None = None
    check_plan: dict | None = None


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
    visibility: str | None = None
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

    def __init__(
        self,
        repo: CheckStore,
        *,
        dice_roller: SecureDiceRoller | None = None,
    ):
        self.repo = repo
        self.dice_roller = dice_roller or SecureDiceRoller()

    def create(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: CreateCheckCommand,
    ) -> dict:
        self._require_kp(identity, campaign_id)
        campaign = self.repo.get_campaign(campaign_id)
        ruleset = get_campaign_ruleset(campaign)
        ruleset.validate_check(
            target=command.target,
            difficulty=command.difficulty,
            bonus_dice=command.bonus_dice,
        )
        manifest = ruleset.manifest
        self.repo.begin_immediate()
        self._assert_parallel_action_check_creation_allowed(
            command.player_action_id
        )
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
            visibility=command.visibility,
            allow_push=command.allow_push,
            roller_member_id=command.roller_member_id,
            pc_id=command.pc_id,
            target=command.target,
            proposal_id=command.proposal_id,
            player_action_id=command.player_action_id,
            check_plan=command.check_plan,
        )

    def list(self, campaign_id: str, identity: AuthenticatedMember) -> list[dict]:
        self._require_campaign(identity, campaign_id)
        checks = self.repo.list_skill_checks(campaign_id, identity.session_id)
        return [check for check in checks if self._can_view(check, identity)]

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
                        visibility=side.visibility,
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
        return [
            contest
            for contest in contests
            if self._can_view(contest["left_check"], identity)
            and self._can_view(contest["right_check"], identity)
        ]

    def resolve_opposed(
        self, opposed_check_id: str, identity: AuthenticatedMember
    ) -> dict:
        opposed = self.repo.get_opposed_check(opposed_check_id)
        self._require_kp(identity, str(opposed["campaign_id"]))
        if identity.session_id != opposed["session_id"]:
            raise PermissionError("Opposed check belongs to another session")
        self.repo.begin_immediate()
        opposed = self.repo.get_opposed_check(opposed_check_id)
        self._require_kp(identity, str(opposed["campaign_id"]))
        if identity.session_id != opposed["session_id"]:
            raise PermissionError("Opposed check belongs to another session")
        left, right = opposed["left_check"], opposed["right_check"]
        self._assert_parallel_check_mutation_allowed(left)
        self._assert_parallel_check_mutation_allowed(right)
        if any(check["status"] not in {"resolved", "overridden"} for check in (left, right)):
            raise ValueError("Both sides must have terminal rolled results")
        if left["ruleset_id"] != right["ruleset_id"]:
            raise ValueError("Opposed check rulesets no longer match")
        ruleset = get_ruleset(
            str(left["ruleset_id"]),
            version=str(left["ruleset_version"]),
        )
        result = ruleset.resolve_opposed_check(
            left_participant_id=str(left["id"]),
            left_target=int(left["target"]),
            left_roll=int(left["selected_roll"]),
            right_participant_id=str(right["id"]),
            right_target=int(right["target"]),
            right_roll=int(right["selected_roll"]),
            left_success_level=(
                str(left["success_level"])
                if left["status"] == "overridden"
                else None
            ),
            right_success_level=(
                str(right["success_level"])
                if right["status"] == "overridden"
                else None
            ),
        )
        result["participant_labels"] = {
            str(left["id"]): left["skill_name"],
            str(right["id"]): right["skill_name"],
        }
        resolved = self.repo.resolve_opposed_check(
            opposed_check_id,
            actor_member_id=identity.member_id,
            result=result,
        )
        winner_id = result.get("winner_id")
        if winner_id:
            winner = left if left["id"] == winner_id else right
            SkillGrowthService(self.repo).sync_check(
                winner,
                actor_member_id=identity.member_id,
                from_opposed=True,
                opposed_check_id=opposed_check_id,
            )
        return resolved

    def reroll_opposed(
        self, opposed_check_id: str, identity: AuthenticatedMember
    ) -> dict:
        opposed = self.repo.get_opposed_check(opposed_check_id)
        self._require_kp(identity, str(opposed["campaign_id"]))
        if identity.session_id != opposed["session_id"]:
            raise PermissionError("Opposed check belongs to another session")
        if opposed["status"] != "reroll_required":
            raise ValueError("Only an exact tied opposed check can be rerolled")
        self.repo.begin_immediate()
        opposed = self.repo.get_opposed_check(opposed_check_id)
        self._require_kp(identity, str(opposed["campaign_id"]))
        if identity.session_id != opposed["session_id"]:
            raise PermissionError("Opposed check belongs to another session")
        if opposed["status"] != "reroll_required":
            raise ValueError("Only an exact tied opposed check can be rerolled")
        self._assert_parallel_check_mutation_allowed(opposed["left_check"])
        self._assert_parallel_check_mutation_allowed(opposed["right_check"])
        # Re-check after acquiring the write lock so two concurrent reroll
        # requests serialize: the second one will see the first's result.
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
                        visibility=str(check["visibility"]),
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
        ruleset = get_ruleset(
            str(check["ruleset_id"]),
            version=str(check["ruleset_version"]),
        )
        if command.input_method == "digital":
            random_roll = self.dice_roller.roll(
                ruleset.build_check_roll_request(int(check["bonus_dice"]))
            )
        elif command.input_method == "physical":
            random_roll = ruleset.build_physical_check_roll(
                bonus_dice=int(check["bonus_dice"]),
                values={
                    "ones_digit": command.ones_digit,
                    "tens_digits": list(command.tens_digits),
                },
            )
        else:
            raise ValueError("Input method must be digital or physical")
        raw_dice = ruleset.check_dice_from_roll(random_roll)
        resolution = ruleset.resolve_check(
            target=int(check["target"]),
            difficulty=check["difficulty"],
            bonus_dice=int(check["bonus_dice"]),
            raw_dice=raw_dice,
        )
        # The batch status and check result form one authority boundary.  Take
        # the writer lock only after potentially slow/random rule evaluation,
        # then re-read both records before the result write.
        self.repo.begin_immediate()
        check = self.repo.get_skill_check(check_id)
        self._require_visible(check, identity, resolving=True)
        self._assert_parallel_check_mutation_allowed(check)
        resolved = self.repo.resolve_skill_check(
            check_id,
            actor_member_id=identity.member_id,
            input_method=command.input_method,
            random_evidence=random_roll.as_dict(),
            resolution=resolution,
        )
        SkillGrowthService(self.repo).sync_check(
            resolved, actor_member_id=identity.member_id
        )
        return resolved

    def replay(self, check_id: str, identity: AuthenticatedMember) -> dict:
        check = self.get(check_id, identity)
        if check["status"] not in {"resolved", "overridden"} or not check["raw_dice"]:
            raise ValueError("Only a resolved check can be replayed")
        ruleset = get_ruleset(
            str(check["ruleset_id"]),
            version=str(check["ruleset_version"]),
        )
        random_evidence = check.get("random_evidence")
        raw_dice = check["raw_dice"]
        if random_evidence:
            raw_dice = ruleset.check_dice_from_roll(
                DiceRollResult.from_dict(random_evidence)
            )
        replayed = ruleset.resolve_check(
            target=int(check["target"]),
            difficulty=check["difficulty"],
            bonus_dice=int(check["bonus_dice"]),
            raw_dice=raw_dice,
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
        self.repo.begin_immediate()
        check = self.repo.get_skill_check(check_id)
        self._require_kp_check(identity, check)
        self._assert_parallel_check_mutation_allowed(check)
        overridden = self.repo.override_skill_check(
            check_id,
            actor_member_id=identity.member_id,
            success_level=success_level,
            passed=passed,
            reason=reason,
        )
        SkillGrowthService(self.repo).sync_check(
            overridden, actor_member_id=identity.member_id
        )
        return overridden

    def cancel(
        self, check_id: str, identity: AuthenticatedMember, *, reason: str
    ) -> dict:
        check = self.repo.get_skill_check(check_id)
        self._require_kp_check(identity, check)
        self.repo.begin_immediate()
        check = self.repo.get_skill_check(check_id)
        self._require_kp_check(identity, check)
        self._assert_parallel_check_mutation_allowed(check)
        return self.repo.cancel_skill_check(
            check_id, actor_member_id=identity.member_id, reason=reason
        )

    def push(self, check_id: str, identity: AuthenticatedMember, *, reason: str) -> dict:
        check = self.repo.get_skill_check(check_id)
        self._require_push_actor(identity, check)
        self.repo.begin_immediate()
        check = self.repo.get_skill_check(check_id)
        self._require_push_actor(identity, check)
        self._assert_parallel_check_mutation_allowed(check)
        return self.repo.push_skill_check(
            check_id, actor_member_id=identity.member_id, reason=reason
        )

    def decline_push(
        self, check_id: str, identity: AuthenticatedMember, *, reason: str
    ) -> dict:
        check = self.repo.get_skill_check(check_id)
        self._require_push_actor(identity, check)
        self.repo.begin_immediate()
        check = self.repo.get_skill_check(check_id)
        self._require_push_actor(identity, check)
        self._assert_parallel_check_mutation_allowed(check)
        return self.repo.decline_skill_check_push(
            check_id, actor_member_id=identity.member_id, reason=reason
        )

    def _assert_parallel_check_mutation_allowed(
        self,
        check: dict,
    ) -> None:
        """Fence a linked result to the batch's check-collection phase.

        The caller must already hold the repository write lock.  Looking up
        every lifecycle status (including settled and superseded) prevents a
        stale check endpoint from changing the exact result behind a ready or
        committed batch receipt.
        """

        self._assert_parallel_action_check_mutation_allowed(
            check.get("player_action_id")
        )

    def _assert_parallel_action_check_creation_allowed(
        self,
        action_id: str | None,
    ) -> None:
        """Keep new batch checks behind the consent-bound workflow seam.

        Initial batch checks are created while the last player confirmation is
        committed. Push children use their own parent-bound repository path.
        The generic KP check APIs have neither authority and therefore cannot
        append another roll after the players have confirmed the batch plan.
        """

        if not action_id:
            return
        batch = self.repo.get_parallel_action_batch_for_action(str(action_id))
        if batch is not None:
            raise ValueError(
                "Checks for a parallel action can be created only by the "
                "consent-bound parallel workflow"
            )

    def _assert_parallel_action_check_mutation_allowed(
        self,
        action_id: str | None,
    ) -> None:
        if not action_id:
            return
        batch = self.repo.get_parallel_action_batch_for_action(str(action_id))
        if batch is None:
            return
        if batch["status"] != "awaiting_checks":
            raise ValueError(
                "Parallel batch check results can change only while the batch "
                f"awaits checks (current status: {batch['status']})"
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
        if not cls._can_view(check, identity):
            raise PermissionError("Check is not visible to this member")

    @classmethod
    def _require_push_actor(
        cls,
        identity: AuthenticatedMember,
        check: dict,
    ) -> None:
        cls._require_campaign(identity, str(check["campaign_id"]))
        if identity.session_id != check["session_id"]:
            raise PermissionError("Check belongs to another session")
        if not cls._can_view(check, identity):
            raise PermissionError("Check is not visible to this member")
        if identity.role != "kp" and identity.member_id != check.get(
            "roller_member_id"
        ):
            raise PermissionError("Only the roller or KP can decide whether to push")

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
        if not cls._can_view(check, identity):
            raise PermissionError("Check is not visible to this member")
        if resolving:
            if identity.role != "kp" and check["roller_member_id"] != identity.member_id:
                raise PermissionError("Only the assigned player can resolve this check")
            if check["status"] != "requested":
                raise ValueError("Only a requested check can be resolved")

    @staticmethod
    def _can_view(check: dict, identity: AuthenticatedMember) -> bool:
        visibility = str(check.get("visibility") or (
            "blind" if check.get("hidden") else "public"
        ))
        is_roller = check.get("roller_member_id") == identity.member_id
        if visibility == "public":
            return True
        if visibility == "private":
            return identity.role == "kp" or is_roller
        if visibility == "blind":
            return identity.role == "kp"
        return False


__all__ = [
    "CheckService",
    "CreateCheckCommand",
    "CreateOpposedCheckCommand",
    "OpposedSideCommand",
    "ResolveCheckCommand",
]
