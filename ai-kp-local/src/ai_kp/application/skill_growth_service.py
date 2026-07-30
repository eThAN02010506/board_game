"""CoC7 runtime skill-growth marks derived from persisted check outcomes."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from ai_kp.application.ports.repositories import SkillGrowthStore
from ai_kp.rulesets import get_ruleset

_EXCLUDED_SKILL_NAMES = {
    "credit rating",
    "cthulhu mythos",
    "信用评级",
    "克苏鲁神话",
}


class SkillGrowthService:
    """Synchronize check results with replayable campaign-state growth marks."""

    def __init__(self, repo: SkillGrowthStore):
        self.repo = repo
        self.ruleset = get_ruleset("coc7")

    def sync_check(
        self,
        check: dict[str, Any],
        *,
        actor_member_id: str,
        from_opposed: bool = False,
        opposed_check_id: str | None = None,
    ) -> dict[str, Any] | None:
        if (
            check.get("ruleset_id") != self.ruleset.manifest.ruleset_id
            or not check.get("investigator_id")
        ):
            return None
        if not from_opposed and self.repo.find_opposed_check_for_skill_check(
            str(check["id"])
        ):
            return None
        # Positive values are bonus dice. Penalty dice do not suppress growth.
        should_mark = bool(check.get("passed")) and int(check.get("bonus_dice") or 0) <= 0
        record = self.repo.get_campaign_investigator(
            str(check["campaign_id"]), str(check["investigator_id"])
        )
        state = record.get("campaign_state")
        revision = record.get("approved_revision")
        if state is None or revision is None:
            return None
        skill = self._eligible_skill(
            revision["canonical_sheet"], str(check.get("skill_key") or "")
        )
        if skill is None:
            return None
        conditions = deepcopy(state.get("conditions") or [])
        changed = self._sync_mark(
            conditions,
            skill=skill,
            source_check_id=str(check["id"]),
            should_mark=should_mark,
        )
        if not changed:
            return None
        action_id = str((check.get("actions") or [{}])[-1].get("id") or check["id"])
        command_id = "skill-growth-" + hashlib.sha256(
            f"{action_id}:{opposed_check_id or ''}:{should_mark}".encode()
        ).hexdigest()
        result = {
            "skill_key": skill["skill_key"],
            "specialization": skill.get("specialization"),
            "source_check_id": check["id"],
            "marked": should_mark,
            "reason": (
                "successful_eligible_check"
                if should_mark
                else "terminal_result_not_growth_eligible"
            ),
        }
        return self.repo.transition_coc7_character_state(
            campaign_id=str(check["campaign_id"]),
            session_id=str(check["session_id"]),
            investigator_id=str(check["investigator_id"]),
            expected_version=int(state["state_version"]),
            changes={"conditions": conditions},
            member_id=actor_member_id,
            command_id=command_id,
            event_type="character.skill_growth_mark",
            command_input={
                "check_id": check["id"],
                "opposed_check_id": opposed_check_id,
            },
            result=result,
            visibility="table",
            ruleset_id=self.ruleset.manifest.ruleset_id,
            ruleset_version=self.ruleset.manifest.version,
            source_reference=dict(self.ruleset.manifest.source_reference),
        )

    @staticmethod
    def _eligible_skill(
        canonical: dict[str, Any], skill_key: str
    ) -> dict[str, Any] | None:
        matches = [
            item
            for item in canonical.get("skills") or []
            if str(item.get("skill_key") or "") == skill_key
        ]
        if len(matches) != 1:
            return None
        skill = matches[0]
        names = {
            str(skill.get("skill_key") or "").strip().casefold(),
            str(skill.get("display_name") or "").strip().casefold(),
        }
        if names & _EXCLUDED_SKILL_NAMES:
            return None
        return skill

    @staticmethod
    def _sync_mark(
        conditions: list[dict[str, Any]],
        *,
        skill: dict[str, Any],
        source_check_id: str,
        should_mark: bool,
    ) -> bool:
        specialization = str(skill.get("specialization") or "").strip()
        mark = next(
            (
                item
                for item in conditions
                if item.get("type") == "skill_growth_mark"
                and item.get("skill_key") == skill["skill_key"]
                and str(item.get("specialization") or "").strip() == specialization
            ),
            None,
        )
        sources = list(mark.get("source_check_ids") or []) if mark else []
        before = list(sources)
        if should_mark and source_check_id not in sources:
            sources.append(source_check_id)
        elif not should_mark and source_check_id in sources:
            sources.remove(source_check_id)
        if sources == before:
            return False
        if mark is not None and not sources:
            conditions.remove(mark)
        elif mark is not None:
            mark["source_check_ids"] = sources
        else:
            conditions.append(
                {
                    "type": "skill_growth_mark",
                    "active": True,
                    "skill_key": skill["skill_key"],
                    "display_name": skill.get("display_name"),
                    "specialization": skill.get("specialization"),
                    "source_check_ids": sources,
                }
            )
        return True


__all__ = ["SkillGrowthService"]
