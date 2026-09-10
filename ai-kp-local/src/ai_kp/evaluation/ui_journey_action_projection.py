"""Strict projection for confirmed direct, check, and multi-step actions."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from ai_kp.evaluation.ui_journey_projection_support import (
    applied_effect_ids,
    canonical_hash,
    json_object,
    projection_observation,
    sql_placeholders,
)
from ai_kp.platform.resolution.action_intent_ir import ActionIntentPlan


def project_resolved_actions(
    connection: sqlite3.Connection,
    *,
    campaign_id: str,
    session_id: str,
    action_ids: tuple[str, ...],
    batches: Mapping[str, dict[str, Any]],
    observations: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    gaps: list[str],
) -> None:
    rows = connection.execute(
        f"""
        SELECT a.id AS action_id, a.member_id, a.status AS action_status,
               a.resolved_at, a.proposal_id, p.public_narration,
               p.applied_at, ad.id AS adjudication_id, ad.mode,
               ad.status AS adjudication_status, ad.selected_skill,
               ae.actor_member_id AS confirmed_by_member_id,
               ae.created_at AS confirmed_at, kr.payload_json AS kernel_json
        FROM player_actions a
        JOIN turn_proposals p ON p.id = a.proposal_id
        JOIN player_action_adjudications ad ON ad.action_id = a.id
        JOIN player_action_adjudication_events ae
          ON ae.adjudication_id = ad.id AND ae.event_type = 'confirmed'
        LEFT JOIN proposal_actions kr
          ON kr.proposal_id = p.id AND kr.action_type = 'kernel_resolution'
        WHERE a.campaign_id = ? AND a.session_id = ?
          AND a.id IN ({sql_placeholders(action_ids)})
          AND a.status = 'resolved' AND p.status = 'approved'
          AND ad.status = 'confirmed'
          AND (SELECT COUNT(*) FROM player_action_adjudication_events one
               WHERE one.adjudication_id = ad.id
                 AND one.event_type = 'confirmed') = 1
          AND (SELECT COUNT(*) FROM proposal_actions one
               WHERE one.proposal_id = p.id
                 AND one.action_type = 'kernel_resolution') <= 1
        ORDER BY ae.created_at, a.id
        """,
        (campaign_id, session_id, *action_ids),
    ).fetchall()
    for row in rows:
        action_id = str(row["action_id"])
        batch = batches.get(action_id)
        if batch is None or row["confirmed_by_member_id"] != row["member_id"]:
            continue
        effect_ids = applied_effect_ids(batch)
        if not effect_ids:
            continue
        narration = str(row["public_narration"] or "").strip()
        if row["mode"] == "direct_resolution" and len(narration) >= 12:
            observations.append(
                projection_observation(
                    row["applied_at"] or row["resolved_at"],
                    "direct_resolution",
                    session_id,
                    action_id=action_id,
                    result_text=narration,
                    applied_effect_ids=effect_ids,
                )
            )

        checks = connection.execute(
            """
            SELECT id, skill_key, skill_name, difficulty, passed, success_level,
                   status, resolved_at, pushed_from_check_id
            FROM skill_checks
            WHERE campaign_id = ? AND session_id = ? AND player_action_id = ?
              AND status IN ('resolved', 'overridden')
              AND pushed_from_check_id IS NULL
            ORDER BY created_at, id
            """,
            (campaign_id, session_id, action_id),
        ).fetchall()
        if row["mode"] == "skill_check":
            _project_check_branches(
                connection,
                row=row,
                checks=checks,
                batch=batch,
                effect_ids=effect_ids,
                session_id=session_id,
                observations=observations,
            )
        _project_bounded_deviation(
            row=row,
            effect_ids=effect_ids,
            session_id=session_id,
            observations=observations,
        )
        facts.append(
            {
                "kind": "action_command_batch",
                "action_id": action_id,
                "adjudication_id": str(row["adjudication_id"]),
                "command_batch_id": str(batch["id"]),
                "expected_version": int(batch["expected_version"]),
                "result_version": int(batch["result_version"]),
            }
        )
    if not batches:
        gaps.append("observed actions have no UI-correlated scenario command batches")


def _project_check_branches(
    connection: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    checks: Sequence[sqlite3.Row],
    batch: Mapping[str, Any],
    effect_ids: list[str],
    session_id: str,
    observations: list[dict[str, Any]],
) -> None:
    selected_skill = str(row["selected_skill"] or "")
    for check in checks:
        if str(check["skill_name"]) != selected_skill and str(check["skill_key"]) != selected_skill:
            continue
        check_id = str(check["id"])
        observations.append(
            projection_observation(
                row["confirmed_at"],
                "skill_confirmed",
                session_id,
                action_id=str(row["action_id"]),
                check_id=check_id,
                skill_id=str(check["skill_key"]),
                difficulty=str(check["difficulty"]),
                confirmed_by_member_id=str(row["confirmed_by_member_id"]),
            )
        )
        consequence = _check_consequence(
            connection,
            action_id=str(row["action_id"]),
            check_id=check_id,
        )
        if consequence is None:
            continue
        text, applied_at = consequence
        if check["passed"] == 1 and str(check["success_level"]) in {
            "regular", "hard", "extreme", "critical"
        } and batch.get("outcome") not in {"failure", "pushed_failure"}:
            observations.append(
                projection_observation(
                    applied_at,
                    "check_success",
                    session_id,
                    action_id=str(row["action_id"]),
                    check_id=check_id,
                    degree=str(check["success_level"]),
                    result_text=text,
                    applied_effect_ids=effect_ids,
                )
            )
            return
        if check["passed"] != 0 or batch.get("outcome") != "failure":
            continue
        before = batch.get("snapshot_before")
        after = batch.get("snapshot")
        if not isinstance(before, Mapping) or not isinstance(after, Mapping):
            continue
        fingerprint_before = canonical_hash(before)
        fingerprint_after = canonical_hash(after)
        if fingerprint_before == fingerprint_after:
            continue
        observations.append(
            projection_observation(
                applied_at,
                "check_failure_cost",
                session_id,
                action_id=str(row["action_id"]),
                check_id=check_id,
                consequence_text=text,
                applied_effect_ids=effect_ids,
                fingerprint_before=fingerprint_before,
                fingerprint_after=fingerprint_after,
            )
        )
        decision = connection.execute(
            """
            SELECT id, actor_member_id, decision FROM skill_check_push_decisions
            WHERE check_id = ?
            """,
            (check_id,),
        ).fetchone()
        if decision is not None and decision["actor_member_id"] == row["member_id"]:
            observations.append(
                projection_observation(
                    row["resolved_at"] or applied_at,
                    "failure_followup",
                    session_id,
                    check_id=check_id,
                    mode=str(decision["decision"]),
                    resolution_id=str(decision["id"]),
                )
            )
        return


def _project_bounded_deviation(
    *,
    row: sqlite3.Row,
    effect_ids: list[str],
    session_id: str,
    observations: list[dict[str, Any]],
) -> None:
    kernel = json_object(row["kernel_json"])
    plan = kernel.get("intent_plan")
    if not isinstance(plan, Mapping):
        return
    try:
        intent_plan = ActionIntentPlan.model_validate(plan)
    except ValueError:
        return
    if len(intent_plan.steps) < 2:
        return
    constraint_ids = [
        requirement.requirement_id
        for step in intent_plan.steps
        for requirement in step.requirements
    ]
    narration = str(row["public_narration"] or "").strip()
    if not constraint_ids or len(narration) < 12:
        return
    observations.append(
        projection_observation(
            row["applied_at"] or row["resolved_at"],
            "bounded_deviation",
            session_id,
            action_id=str(row["action_id"]),
            proposal_id=str(row["proposal_id"]),
            constraint_ids=list(dict.fromkeys(constraint_ids)),
            confirmed_by_member_id=str(row["confirmed_by_member_id"]),
            result_text=narration,
            applied_effect_ids=effect_ids,
        )
    )


def _check_consequence(
    connection: sqlite3.Connection, *, action_id: str, check_id: str
) -> tuple[str, Any] | None:
    rows = connection.execute(
        """
        SELECT p.public_narration, p.applied_at, pa.payload_json
        FROM proposal_actions pa
        JOIN turn_proposals p ON p.id = pa.proposal_id
        WHERE pa.action_type = 'check_consequence_basis'
          AND p.status = 'approved' AND p.applied_at IS NOT NULL
        ORDER BY p.applied_at, p.id
        """
    ).fetchall()
    for row in rows:
        payload = json_object(row["payload_json"])
        fingerprint = payload.get("result_fingerprint")
        if (
            payload.get("player_action_id") == action_id
            and check_id in payload.get("check_ids", [])
            and isinstance(fingerprint, str)
            and len(fingerprint) == 64
            and all(character in "0123456789abcdef" for character in fingerprint)
            and len(str(row["public_narration"] or "").strip()) >= 12
        ):
            return str(row["public_narration"]).strip(), row["applied_at"]
    return None


__all__ = ["project_resolved_actions"]
