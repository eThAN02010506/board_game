"""SQLite adapter for replayable ruleset checks and their append-only audit."""

import json
import sqlite3
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.resolution import build_check_consequence_snapshot

_SUCCESS_LEVEL_RANK = {
    "fumble": -1,
    "failure": 0,
    "regular": 1,
    "hard": 2,
    "extreme": 3,
    "critical": 4,
}
_DIFFICULTY_RANK = {"regular": 1, "hard": 2, "extreme": 3}


def _validate_override_result(
    *,
    difficulty: str,
    success_level: str,
    passed: bool,
) -> None:
    if success_level not in _SUCCESS_LEVEL_RANK:
        raise ValueError("Unsupported success level")
    if difficulty not in _DIFFICULTY_RANK:
        raise ValueError("Unsupported check difficulty")
    expected_passed = (
        _SUCCESS_LEVEL_RANK[success_level] >= _DIFFICULTY_RANK[difficulty]
    )
    if type(passed) is not bool or passed is not expected_passed:
        raise ValueError(
            "Override passed value contradicts its success level and difficulty"
        )


class SkillCheckRepository(SQLiteRepository):
    """Persist replayable checks without selecting or executing a ruleset."""

    connection: sqlite3.Connection

    def create_skill_check(
        self,
        *,
        campaign_id: str,
        session_id: str,
        requested_by_member_id: str,
        skill_name: str,
        difficulty: str,
        ruleset_id: str,
        ruleset_version: str,
        source_reference: dict[str, Any],
        bonus_dice: int = 0,
        hidden: bool = False,
        allow_push: bool = True,
        roller_member_id: str | None = None,
        pc_id: str | None = None,
        target: int | None = None,
        proposal_id: str | None = None,
        player_action_id: str | None = None,
        pushed_from_check_id: str | None = None,
    ) -> dict[str, Any]:
        self._validate_check_scope(campaign_id, session_id, requested_by_member_id)
        self._validate_check_origin(
            campaign_id=campaign_id,
            session_id=session_id,
            proposal_id=proposal_id,
            player_action_id=player_action_id,
        )
        normalized_skill = skill_name.strip()
        if not normalized_skill:
            raise ValueError("Skill name is required")
        roller_member_id, pc_id = self._resolve_check_roller(
            session_id, roller_member_id, pc_id
        )
        target_values = self._check_target_values(
            campaign_id, pc_id, normalized_skill, target
        )
        check_id = new_id("check")
        self.connection.execute(
            """
            INSERT INTO skill_checks
              (id, campaign_id, session_id, proposal_id, player_action_id,
               requested_by_member_id, roller_member_id, pc_id, investigator_id,
               skill_key, skill_name, target, target_source, difficulty, bonus_dice,
               hidden, allow_push, pushed_from_check_id, ruleset_id, ruleset_version,
               source_reference_json, investigator_state_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                check_id, campaign_id, session_id, proposal_id, player_action_id,
                requested_by_member_id, roller_member_id, pc_id,
                target_values["investigator_id"], target_values["skill_key"],
                normalized_skill, target_values["target"], target_values["target_source"],
                difficulty, bonus_dice, int(hidden), int(allow_push),
                pushed_from_check_id, ruleset_id, ruleset_version,
                json.dumps(source_reference, ensure_ascii=False),
                target_values["state_version"],
            ),
        )
        self._add_check_action(check_id, "requested", requested_by_member_id)
        self._append_check_realtime(check_id, "check.requested")
        return self.get_skill_check(check_id)

    def _validate_check_scope(
        self, campaign_id: str, session_id: str, requester_id: str
    ) -> None:
        session = self.get_campaign_session(session_id)
        if session["campaign_id"] != campaign_id or session["status"] != "active":
            raise ValueError("Check requires the campaign's active session")
        requester = self.get_session_member(requester_id)
        if requester["session_id"] != session_id or requester["revoked_at"] is not None:
            raise ValueError("Check requester must be active in this session")

    def _validate_check_origin(
        self,
        *,
        campaign_id: str,
        session_id: str,
        proposal_id: str | None,
        player_action_id: str | None,
    ) -> None:
        if proposal_id is None and player_action_id is None:
            return
        if proposal_id is None:
            raise ValueError("A linked player action requires its origin proposal")
        proposal = self.connection.execute(
            """
            SELECT campaign_id, status FROM turn_proposals
            WHERE id = ?
            """,
            (proposal_id,),
        ).fetchone()
        if (
            proposal is None
            or proposal["campaign_id"] != campaign_id
            or proposal["status"] != "approved"
        ):
            raise ValueError(
                "A linked check requires an approved origin proposal "
                "in the same campaign"
            )
        if player_action_id is None:
            return
        action = self.connection.execute(
            """
            SELECT campaign_id, session_id, status, proposal_id
            FROM player_actions
            WHERE id = ?
            """,
            (player_action_id,),
        ).fetchone()
        if (
            action is None
            or action["campaign_id"] != campaign_id
            or action["session_id"] != session_id
            or action["status"] != "reviewed"
            or action["proposal_id"] != proposal_id
        ):
            raise ValueError(
                "A linked check requires a reviewed player action whose "
                "campaign, session, and origin proposal all match"
            )

    def _resolve_check_roller(
        self,
        session_id: str,
        roller_member_id: str | None,
        pc_id: str | None,
    ) -> tuple[str | None, str | None]:
        if roller_member_id:
            roller = self.get_session_member(roller_member_id)
            if (
                roller["session_id"] != session_id
                or roller["role"] != "player"
                or roller["revoked_at"] is not None
            ):
                raise ValueError("Roller must be an active player in this session")
            if pc_id is None:
                pc_id = str(roller["pc_id"]) if roller["pc_id"] else None
            elif roller["pc_id"] != pc_id:
                raise ValueError("Selected PC is not controlled by the roller")
        elif pc_id:
            row = self.connection.execute(
                """
                SELECT id FROM session_members
                WHERE session_id = ? AND pc_id = ? AND role = 'player'
                  AND revoked_at IS NULL
                """,
                (session_id, pc_id),
            ).fetchone()
            roller_member_id = str(row["id"]) if row is not None else None
        return roller_member_id, pc_id

    def _check_target_values(
        self,
        campaign_id: str,
        pc_id: str | None,
        normalized_skill: str,
        target: int | None,
    ) -> dict[str, Any]:
        target_details = self._resolve_check_target(campaign_id, pc_id, normalized_skill)
        if target is None:
            if target_details is None:
                raise ValueError("No approved character skill target was found; KP must set one")
            return {
                "target": int(target_details["target"]),
                "target_source": str(target_details["target_source"]),
                "investigator_id": target_details.get("investigator_id"),
                "state_version": target_details.get("state_version"),
                "skill_key": str(target_details.get("skill_key") or normalized_skill),
            }
        if not 0 <= target <= 100:
            raise ValueError("Check target must be between 0 and 100")
        details = target_details or {}
        return {
            "target": target,
            "target_source": "kp_manual",
            "investigator_id": details.get("investigator_id"),
            "state_version": details.get("state_version"),
            "skill_key": str(details.get("skill_key") or normalized_skill),
        }

    def get_skill_check(self, check_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM skill_checks WHERE id = ?", (check_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Skill check not found: {check_id}")
        result = row_to_dict(row)
        result["hidden"] = bool(result["hidden"])
        result["allow_push"] = bool(result["allow_push"])
        result["passed"] = bool(result["passed"]) if result["passed"] is not None else None
        raw_dice_json = result.pop("raw_dice_json")
        result["raw_dice"] = (
            decode_json_field(raw_dice_json, None) if raw_dice_json is not None else None
        )
        result["source_reference"] = decode_json_field(
            result.pop("source_reference_json"), {}
        )
        original_result_json = result.pop("original_result_json")
        result["original_result"] = (
            decode_json_field(original_result_json, None)
            if original_result_json is not None
            else None
        )
        result["actions"] = self.list_skill_check_actions(check_id)
        return result

    def list_skill_checks(self, campaign_id: str, session_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id FROM skill_checks
            WHERE campaign_id = ? AND session_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (campaign_id, session_id),
        ).fetchall()
        return [self.get_skill_check(str(row["id"])) for row in rows]

    def list_skill_check_actions(self, check_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM skill_check_actions
            WHERE check_id = ? ORDER BY created_at, id
            """,
            (check_id,),
        ).fetchall()
        result = []
        for row in rows:
            action = row_to_dict(row)
            action["payload"] = decode_json_field(action.pop("payload_json"), {})
            result.append(action)
        return result

    def resolve_skill_check(
        self,
        check_id: str,
        *,
        actor_member_id: str,
        input_method: str,
        resolution: dict[str, Any],
    ) -> dict[str, Any]:
        check = self.get_skill_check(check_id)
        if check["status"] != "requested":
            raise ValueError("Only a requested check can be resolved")
        if input_method not in {"digital", "physical"}:
            raise ValueError("Unsupported check input method")
        updated = self.connection.execute(
            """
            UPDATE skill_checks
            SET status = 'resolved', input_method = ?, raw_dice_json = ?,
                selected_roll = ?, threshold = ?, success_level = ?, passed = ?,
                resolved_by_member_id = ?, resolved_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'requested'
            """,
            (
                input_method,
                json.dumps(resolution["raw_dice"], ensure_ascii=False),
                resolution["selected_roll"],
                resolution["threshold"],
                resolution["success_level"],
                int(resolution["passed"]),
                actor_member_id,
                check_id,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Check was already resolved")
        self._add_check_action(
            check_id,
            "resolved",
            actor_member_id,
            payload={
                "input_method": input_method,
                "selected_roll": resolution["selected_roll"],
                "success_level": resolution["success_level"],
                "passed": resolution["passed"],
            },
        )
        self._append_check_realtime(check_id, "check.resolved")
        self._notify_consequence_ready_when_checks_terminal(check_id)
        return self.get_skill_check(check_id)

    def override_skill_check(
        self,
        check_id: str,
        *,
        actor_member_id: str,
        success_level: str,
        passed: bool,
        reason: str,
    ) -> dict[str, Any]:
        # Serialize the finalized-action check with the override write. Without
        # this lock, consequence approval could resolve the action between the
        # read below and the skill-check update.
        self.begin_immediate()
        check = self.get_skill_check(check_id)
        self._assert_check_consequence_not_finalized(check)
        if check["status"] not in {"resolved", "overridden"}:
            raise ValueError("A check must be resolved before KP override")
        pushed_child = self.connection.execute(
            "SELECT id FROM skill_checks WHERE pushed_from_check_id = ?",
            (check_id,),
        ).fetchone()
        if pushed_child is not None:
            raise ValueError("A check with a pushed child cannot be overridden")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("KP override reason is required")
        _validate_override_result(
            difficulty=str(check["difficulty"]),
            success_level=success_level,
            passed=passed,
        )
        original_result = check.get("original_result") or {
            "selected_roll": check["selected_roll"],
            "threshold": check["threshold"],
            "success_level": check["success_level"],
            "passed": check["passed"],
        }
        self.connection.execute(
            """
            UPDATE skill_checks
            SET status = 'overridden', success_level = ?, passed = ?,
                resolved_by_member_id = ?, override_reason = ?,
                original_result_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                success_level,
                int(passed),
                actor_member_id,
                normalized_reason,
                json.dumps(original_result, ensure_ascii=False),
                check_id,
            ),
        )
        self._add_check_action(
            check_id,
            "overridden",
            actor_member_id,
            reason=normalized_reason,
            payload={"success_level": success_level, "passed": passed},
        )
        self._append_check_realtime(check_id, "check.overridden")
        self._notify_consequence_ready_when_checks_terminal(check_id)
        return self.get_skill_check(check_id)

    def cancel_skill_check(
        self, check_id: str, *, actor_member_id: str, reason: str
    ) -> dict[str, Any]:
        # Resolution and cancellation are competing terminal transitions. Take
        # the writer lock before reading, then keep the status predicate on the
        # update as a second compare-and-swap guard.
        self.begin_immediate()
        check = self.get_skill_check(check_id)
        self._assert_check_consequence_not_finalized(check)
        if check["status"] != "requested":
            raise ValueError("Only a requested check can be cancelled")
        updated = self.connection.execute(
            """
            UPDATE skill_checks SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'requested'
            """,
            (check_id,),
        )
        if updated.rowcount != 1:
            raise ValueError("Check changed before it could be cancelled")
        self._add_check_action(
            check_id, "cancelled", actor_member_id, reason=reason.strip()
        )
        self._append_check_realtime(check_id, "check.cancelled")
        self._notify_consequence_ready_when_checks_terminal(check_id)
        return self.get_skill_check(check_id)

    def push_skill_check(
        self, check_id: str, *, actor_member_id: str, reason: str
    ) -> dict[str, Any]:
        # Keep the finalized-action guard and child creation in one serialized
        # transaction for the same reason as KP override.
        self.begin_immediate()
        check = self.get_skill_check(check_id)
        self._assert_check_consequence_not_finalized(check)
        if check["status"] not in {"resolved", "overridden"} or check["passed"]:
            raise ValueError("Only a failed resolved check can be pushed")
        if not check["allow_push"]:
            raise ValueError("This check cannot be pushed")
        existing = self.connection.execute(
            "SELECT id FROM skill_checks WHERE pushed_from_check_id = ?",
            (check_id,),
        ).fetchone()
        if existing is not None:
            raise ValueError("This check has already been pushed")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("Push consequence or reason is required")
        child = self.create_skill_check(
            campaign_id=str(check["campaign_id"]),
            session_id=str(check["session_id"]),
            requested_by_member_id=actor_member_id,
            skill_name=str(check["skill_name"]),
            difficulty=str(check["difficulty"]),
            ruleset_id=str(check["ruleset_id"]),
            ruleset_version=str(check["ruleset_version"]),
            source_reference=dict(check["source_reference"]),
            bonus_dice=int(check["bonus_dice"]),
            hidden=bool(check["hidden"]),
            allow_push=False,
            roller_member_id=check.get("roller_member_id"),
            pc_id=check.get("pc_id"),
            target=int(check["target"]),
            proposal_id=check.get("proposal_id"),
            player_action_id=check.get("player_action_id"),
            pushed_from_check_id=check_id,
        )
        self._add_check_action(
            check_id,
            "pushed",
            actor_member_id,
            reason=normalized_reason,
            payload={"child_check_id": child["id"]},
        )
        return child

    def _resolve_check_target(
        self, campaign_id: str, pc_id: str | None, skill_name: str
    ) -> dict[str, Any] | None:
        if not pc_id:
            return None
        row = self.connection.execute(
            """
            SELECT ci.investigator_id, ir.canonical_json, ics.state_version
            FROM campaign_investigators ci
            JOIN investigator_revisions ir ON ir.id = ci.approved_revision_id
            LEFT JOIN investigator_campaign_state ics
              ON ics.campaign_id = ci.campaign_id
             AND ics.investigator_id = ci.investigator_id
            WHERE ci.campaign_id = ? AND ci.legacy_pc_id = ?
              AND ci.status = 'approved'
            """,
            (campaign_id, pc_id),
        ).fetchone()
        if row is not None:
            canonical = decode_json_field(row["canonical_json"], {})
            wanted = skill_name.casefold()
            for skill in canonical.get("skills") or []:
                names = {
                    str(skill.get("skill_key") or "").casefold(),
                    str(skill.get("display_name") or "").casefold(),
                    str(skill.get("specialization") or "").casefold(),
                }
                if wanted in names:
                    return {
                        "target": int(skill.get("current_value") or 0),
                        "target_source": "approved_investigator_revision",
                        "investigator_id": str(row["investigator_id"]),
                        "state_version": row["state_version"],
                        "skill_key": str(skill.get("skill_key") or skill_name),
                    }
            characteristics = canonical.get("characteristics") or {}
            for key, value in characteristics.items():
                if str(key).casefold() == wanted:
                    return {
                        "target": int(value),
                        "target_source": "approved_investigator_revision",
                        "investigator_id": str(row["investigator_id"]),
                        "state_version": row["state_version"],
                        "skill_key": str(key),
                    }
        legacy = self.connection.execute(
            "SELECT sheet_json FROM player_characters WHERE id = ? AND campaign_id = ?",
            (pc_id, campaign_id),
        ).fetchone()
        if legacy is None:
            return None
        sheet = decode_json_field(legacy["sheet_json"], {})
        value = sheet.get(skill_name)
        if value is None and isinstance(sheet.get("skills"), dict):
            value = sheet["skills"].get(skill_name)
        if isinstance(value, (int, float)) and 0 <= int(value) <= 100:
            return {
                "target": int(value),
                "target_source": "legacy_pc_sheet",
                "skill_key": skill_name,
            }
        return None

    def _add_check_action(
        self,
        check_id: str,
        action_type: str,
        actor_member_id: str,
        *,
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO skill_check_actions
              (id, check_id, action_type, actor_member_id, reason, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("checkaction"),
                check_id,
                action_type,
                actor_member_id,
                reason,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )

    def player_action_id_for_proposal(self, proposal_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT id FROM player_actions WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return str(row["id"]) if row is not None else None

    def list_skill_checks_for_action(
        self,
        player_action_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id FROM skill_checks
            WHERE player_action_id = ?
            ORDER BY created_at, id
            """,
            (player_action_id,),
        ).fetchall()
        return [self.get_skill_check(str(row["id"])) for row in rows]

    def _assert_check_consequence_not_finalized(self, check: dict[str, Any]) -> None:
        action_id = check.get("player_action_id")
        if not action_id:
            return
        row = self.connection.execute(
            "SELECT status FROM player_actions WHERE id = ?",
            (action_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Linked player action no longer exists")
        if row["status"] in {"resolved", "rejected"}:
            raise ValueError(
                "The check consequence is already finalized and cannot be changed"
            )

    def _notify_consequence_ready_when_checks_terminal(self, check_id: str) -> None:
        check = self.get_skill_check(check_id)
        action_id = check.get("player_action_id")
        if not action_id:
            return
        action = self.connection.execute(
            "SELECT status FROM player_actions WHERE id = ?",
            (action_id,),
        ).fetchone()
        if action is None or action["status"] != "reviewed":
            return
        checks = self.list_skill_checks_for_action(str(action_id))
        if not checks or any(item["status"] == "requested" for item in checks):
            return
        snapshot = build_check_consequence_snapshot(checks)
        self.append_realtime_event(
            session_id=str(check["session_id"]),
            campaign_id=str(check["campaign_id"]),
            audience="kp",
            event_type="check.consequence_ready",
            resource_type="player_action",
            resource_id=str(action_id),
            payload={
                "status": "reviewed",
                "result_fingerprint": snapshot["result_fingerprint"],
            },
        )

    def _append_check_realtime(self, check_id: str, event_type: str) -> None:
        check = self.get_skill_check(check_id)
        self.append_realtime_event(
            session_id=str(check["session_id"]),
            campaign_id=str(check["campaign_id"]),
            audience="kp" if check["hidden"] else "session",
            event_type=event_type,
            resource_type="skill_check",
            resource_id=check_id,
            payload={"status": check["status"]},
        )


__all__ = ["SkillCheckRepository"]
