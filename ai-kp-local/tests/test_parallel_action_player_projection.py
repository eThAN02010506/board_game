from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from ai_kp.api.schemas import ParallelActionPlayerBatchResponse
from ai_kp.application.parallel_action_player_projection import (
    ParallelActionPlayerProjectionService,
)
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember
from tests.test_parallel_action_batches import _create_batch, _prepared_direct_batch

OWN_MEMBER_ID = "member-own"
OTHER_MEMBER_ID = "member-other"
CAMPAIGN_ID = "campaign-safe"
SESSION_ID = "session-safe"


def _identity(
    member_id: str = OWN_MEMBER_ID,
    *,
    campaign_id: str = CAMPAIGN_ID,
    session_id: str = SESSION_ID,
    role: str = "player",
) -> AuthenticatedMember:
    return AuthenticatedMember(
        member_id=member_id,
        session_id=session_id,
        campaign_id=campaign_id,
        role=role,
        display_name=member_id,
        pc_id=f"pc-{member_id}",
    )


def _check(
    check_id: str,
    *,
    visibility: str = "public",
    roller_member_id: str = OWN_MEMBER_ID,
    status: str = "requested",
    passed: bool | None = None,
    allow_push: bool = True,
    push_decision: dict[str, Any] | None = None,
    pushed_from_check_id: str | None = None,
) -> dict[str, Any]:
    resolved = status in {"resolved", "overridden"}
    return {
        "id": check_id,
        "campaign_id": CAMPAIGN_ID,
        "session_id": SESSION_ID,
        "proposal_id": "proposal-own",
        "player_action_id": "action-own",
        "requested_by_member_id": "kp-secret-id",
        "roller_member_id": roller_member_id,
        "pc_id": "pc-own",
        "skill_key": "coc7.spot_hidden",
        "skill_name": "侦查",
        "target": 60,
        "difficulty": "regular",
        "bonus_dice": 0,
        "hidden": visibility != "public",
        "visibility": visibility,
        "allow_push": allow_push,
        "pushed_from_check_id": pushed_from_check_id,
        "status": status,
        "input_method": "digital" if resolved else None,
        "raw_dice": (
            {
                "ones_digit": 8,
                "tens_digits": [7],
                "candidates": [78],
                "SECRET_RAW_DICE": "SECRET_RAW_DICE",
            }
            if resolved
            else None
        ),
        "random_evidence": {
            "evidence_fingerprint": "SECRET_RESULT_HASH",
        },
        "selected_roll": 78 if resolved else None,
        "threshold": 60 if resolved else None,
        "success_level": "failure" if resolved and passed is False else None,
        "passed": passed,
        "check_plan": {
            "scope": "desk",
            "supporting_factors": ["lamp"],
            "automatic_information": [],
            "failure_stakes": "time passes",
            "pushed_failure_stakes": "the evidence is damaged",
            "SECRET_CHECK_PLAN": "SECRET_CHECK_PLAN",
        },
        "push_decision": push_decision,
        "created_at": "2026-08-21 10:00:00",
        "resolved_at": "2026-08-21 10:01:00" if resolved else None,
        "result_fingerprint": "SECRET_RESULT_HASH",
        "actions": [{"payload": "SECRET_CHECK_EVENT"}],
    }


def _adjudication(*, status: str = "pending") -> dict[str, Any]:
    return {
        "id": "adjudication-own",
        "action_id": "action-own",
        "proposal_id": "proposal-own",
        "mode": "skill_check",
        "status": status,
        "version": 3,
        "reason": "A roll determines whether the search succeeds.",
        "prompt": "Confirm the skill or revise the action.",
        "skill_options": [
            {
                "skill_name": "侦查",
                "skill_key": "coc7.spot_hidden",
                "target": 60,
                "difficulty": "regular",
                "reason": "Search the desk carefully.",
                "hidden": True,
                "bonus_dice": 0,
                "allow_push": True,
                "scope": "desk",
                "supporting_factors": [],
                "automatic_information": [],
                "failure_stakes": "time passes",
                "pushed_failure_stakes": "the evidence is damaged",
                "SECRET_OPTION": "SECRET_OPTION",
            }
        ],
        "selected_skill": "侦查",
        "source_model": "kernel:test",
        "source_error": "SECRET_SOURCE_ERROR",
        "kp_notes": "SECRET_KP_NOTES",
        "events": [{"payload": "SECRET_ADJUDICATION_EVENT"}],
        "tabletop_turn": {
            "schema_version": "tabletop-turn.v1",
            "route": "roleplay",
            "attempt_count": 2,
            "audit_count": 1,
            "audit_reason": "SECRET_INTERNAL_REASON",
            "validation_errors": ["SECRET_RAW_VALIDATION"],
            "frame": {
                "kind": "npc_dialogue",
                "goal": "question the witness",
                "method": "conversation",
                "target_entity_ids": ["npc-witness"],
                "dialogue": "What did you see?",
                "steps": [],
                "time_span": "",
                "ambiguity": None,
                "confidence": "high",
            },
            "response": {
                "basis_hash": "SECRET_AUTHORITY_HASH",
                "public_narration": "The witness answers cautiously.",
                "speaker_entity_ids": ["npc-witness"],
                "source": "deterministic",
                "attempt_count": 2,
                "validation_errors": ["SECRET_PROVIDER_ERROR"],
                "actor_traces": [{
                    "schema_version": "entity-actor-trace.v1",
                    "entity_id": "npc-witness",
                    "entity_title": "Witness",
                    "execution": "deterministic_fallback",
                    "generation_attempt_count": 2,
                    "error_codes": ["actor_output_rejected"],
                    "private_prompt": "SECRET_ACTOR_PROMPT",
                }],
            },
        },
        "updated_at": "2026-08-21 10:00:00",
        "confirmed_at": (
            "2026-08-21 10:00:30" if status == "confirmed" else None
        ),
        "ruling": {
            "goal": "search the desk",
            "method": "careful visual inspection",
            "target": "desk",
            "feasibility": "possible",
            "resolution": "check",
            "maximum_effect": "find evidence within the desk",
            "alternative": "describe a different method",
            "internal_basis": "SECRET_RULING_BASIS",
        },
    }


def _batch(
    *,
    status: str = "awaiting_confirmation",
    own_adjudication_status: str = "pending",
    other_adjudication_status: str = "pending",
    checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    own_adjudication = _adjudication(status=own_adjudication_status)
    return {
        "id": "parallel-batch-safe",
        "campaign_id": CAMPAIGN_ID,
        "session_id": SESSION_ID,
        "status": status,
        "version": 8,
        "updated_at": "2026-08-21 10:02:00",
        "settled_at": "2026-08-21 10:03:00" if status == "settled" else None,
        "attention_reason": "SECRET_ATTENTION_REASON",
        "preparation_hash": "SECRET_PREVIEW_HASH",
        "scenario_command_batch_id": "SECRET_COMMAND_BATCH",
        "events": [{"payload": "SECRET_BATCH_EVENT"}],
        "items": [
            {
                "action_id": "action-own",
                "proposal_id": "proposal-own",
                "adjudication_id": "adjudication-own",
                "operator_id": "SECRET_OPERATOR",
                "preview_hash": "SECRET_PREVIEW_HASH",
                "preview": {"commands": ["SECRET_COMMAND"]},
                "outcome_key": "SECRET_OUTCOME",
                "adjudication": {
                    "status": own_adjudication_status,
                    "mode": "skill_check",
                },
                "checks": checks or [],
            },
            {
                "action_id": "SECRET_OTHER_ACTION_ID",
                "proposal_id": "proposal-other",
                "adjudication_id": "adjudication-other",
                "operator_id": "SECRET_OTHER_SKILL",
                "outcome_key": "SECRET_OTHER_OUTCOME",
                "adjudication": {
                    "status": other_adjudication_status,
                    "mode": "skill_check",
                    "selected_skill": "SECRET_OTHER_SKILL",
                },
                "checks": [{"id": "SECRET_OTHER_CHECK_ID"}],
            },
        ],
        "_full_adjudication": own_adjudication,
    }


class _ProjectionRepo:
    def __init__(self, batch: dict[str, Any]):
        self.batch = deepcopy(batch)
        self.members = {
            OWN_MEMBER_ID: {
                "id": OWN_MEMBER_ID,
                "campaign_id": CAMPAIGN_ID,
                "session_id": SESSION_ID,
                "role": "player",
                "revoked_at": None,
            },
            OTHER_MEMBER_ID: {
                "id": OTHER_MEMBER_ID,
                "campaign_id": CAMPAIGN_ID,
                "session_id": SESSION_ID,
                "role": "player",
                "revoked_at": None,
            },
            "member-observer": {
                "id": "member-observer",
                "campaign_id": CAMPAIGN_ID,
                "session_id": SESSION_ID,
                "role": "player",
                "revoked_at": None,
            },
        }
        self.actions = {
            "action-own": {
                "id": "action-own",
                "campaign_id": CAMPAIGN_ID,
                "session_id": SESSION_ID,
                "member_id": OWN_MEMBER_ID,
                "proposal_id": "proposal-own",
            },
            "SECRET_OTHER_ACTION_ID": {
                "id": "SECRET_OTHER_ACTION_ID",
                "campaign_id": CAMPAIGN_ID,
                "session_id": SESSION_ID,
                "member_id": OTHER_MEMBER_ID,
                "proposal_id": "proposal-other",
            },
        }

    def get_parallel_action_batch(self, batch_id: str) -> dict[str, Any]:
        assert batch_id == self.batch["id"]
        return deepcopy(self.batch)

    def get_session_member(self, member_id: str) -> dict[str, Any]:
        return deepcopy(self.members[member_id])

    def get_player_action(self, action_id: str) -> dict[str, Any]:
        return deepcopy(self.actions[action_id])

    def get_action_adjudication(self, action_id: str) -> dict[str, Any]:
        assert action_id == "action-own"
        return deepcopy(self.batch["_full_adjudication"])


def _project(batch: dict[str, Any]) -> dict[str, Any]:
    projection = ParallelActionPlayerProjectionService(_ProjectionRepo(batch)).get(
        "parallel-batch-safe",
        _identity(),
    )
    payload = projection.as_dict()
    ParallelActionPlayerBatchResponse.model_validate(payload)
    return payload


def test_projection_is_an_exact_recursive_allowlist_and_hides_blind_checks() -> None:
    batch = _batch(
        checks=[
            _check("check-public"),
            _check("check-private", visibility="private"),
            _check(
                "SECRET_PRIVATE_OTHER_CHECK_ID",
                visibility="private",
                roller_member_id=OTHER_MEMBER_ID,
            ),
            _check("SECRET_BLIND_CHECK_ID", visibility="blind"),
        ]
    )

    payload = _project(batch)

    assert set(payload) == {
        "id",
        "status",
        "version",
        "participant_count",
        "confirmed_count",
        "waiting_count",
        "self_phase",
        "own_item",
        "updated_at",
        "settled_at",
        "public_message",
    }
    assert set(payload["own_item"]) == {"action_id", "adjudication", "checks"}
    assert [check["id"] for check in payload["own_item"]["checks"]] == [
        "check-public",
        "check-private",
    ]
    assert "hidden" not in payload["own_item"]["adjudication"]["skill_options"][0]
    tabletop = payload["own_item"]["adjudication"]["tabletop_turn"]
    assert tabletop["response"]["actor_traces"][0]["error_codes"] == [
        "actor_output_rejected"
    ]

    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    for sentinel in (
        "SECRET_OTHER_ACTION_ID",
        "SECRET_OTHER_SKILL",
        "SECRET_OTHER_CHECK_ID",
        "SECRET_OTHER_OUTCOME",
        "SECRET_PRIVATE_OTHER_CHECK_ID",
        "SECRET_BLIND_CHECK_ID",
        "SECRET_ATTENTION_REASON",
        "SECRET_PREVIEW_HASH",
        "SECRET_COMMAND_BATCH",
        "SECRET_BATCH_EVENT",
        "SECRET_OPERATOR",
        "SECRET_COMMAND",
        "SECRET_OUTCOME",
        "SECRET_SOURCE_ERROR",
        "SECRET_KP_NOTES",
        "SECRET_ADJUDICATION_EVENT",
        "SECRET_INTERNAL_REASON",
        "SECRET_RAW_VALIDATION",
        "SECRET_AUTHORITY_HASH",
        "SECRET_PROVIDER_ERROR",
        "SECRET_ACTOR_PROMPT",
        "SECRET_OPTION",
        "SECRET_RULING_BASIS",
        "SECRET_RESULT_HASH",
        "SECRET_CHECK_EVENT",
        "SECRET_CHECK_PLAN",
        "SECRET_RAW_DICE",
    ):
        assert sentinel not in serialized


@pytest.mark.parametrize(
    ("batch_status", "adjudication_status", "checks", "expected_phase"),
    [
        ("awaiting_confirmation", "pending", [], "awaiting_confirmation"),
        ("awaiting_confirmation", "confirmed", [], "waiting_for_others"),
        (
            "awaiting_checks",
            "confirmed",
            [_check("check-requested")],
            "awaiting_check",
        ),
        (
            "awaiting_checks",
            "confirmed",
            [_check("check-failed", status="resolved", passed=False)],
            "awaiting_push_decision",
        ),
        (
            "awaiting_checks",
            "confirmed",
            [
                _check(
                    "check-accepted",
                    status="resolved",
                    passed=False,
                    push_decision={
                        "decision": "accept_failure",
                        "reason": "accept",
                        "created_at": "2026-08-21 10:02:00",
                    },
                )
            ],
            "waiting_for_checks",
        ),
        (
            "awaiting_checks",
            "confirmed",
            [_check("blind-requested", visibility="blind")],
            "waiting_for_checks",
        ),
        ("ready", "confirmed", [], "ready"),
        ("committing", "confirmed", [], "settling"),
        ("settled", "confirmed", [], "settled"),
        ("needs_attention", "confirmed", [], "needs_attention"),
        ("superseded", "superseded", [], "superseded"),
    ],
)
def test_projection_derives_member_phase_without_internal_reasons(
    batch_status: str,
    adjudication_status: str,
    checks: list[dict[str, Any]],
    expected_phase: str,
) -> None:
    payload = _project(
        _batch(
            status=batch_status,
            own_adjudication_status=adjudication_status,
            other_adjudication_status=(
                "confirmed" if batch_status != "awaiting_confirmation" else "pending"
            ),
            checks=checks,
        )
    )

    assert payload["self_phase"] == expected_phase
    assert "SECRET_ATTENTION_REASON" not in payload["public_message"]


def test_projection_reports_only_aggregate_confirmation_progress() -> None:
    payload = _project(
        _batch(
            own_adjudication_status="confirmed",
            other_adjudication_status="pending",
        )
    )

    assert payload["participant_count"] == 2
    assert payload["confirmed_count"] == 1
    assert payload["waiting_count"] == 1


def test_projection_maps_canonical_skill_key_to_player_display_name() -> None:
    batch = _batch()
    own_item = next(
        item for item in batch["items"] if item["action_id"] == "action-own"
    )
    own_item["adjudication"]["selected_skill"] = "coc7.spot_hidden"

    payload = _project(batch)

    assert payload["own_item"]["adjudication"]["selected_skill"] == "侦查"


def test_projection_rejects_active_non_participant() -> None:
    repo = _ProjectionRepo(_batch())

    with pytest.raises(PermissionError, match="participating action owner"):
        ParallelActionPlayerProjectionService(repo).get(
            "parallel-batch-safe",
            _identity("member-observer"),
        )


def test_projection_rejects_cross_session_identity() -> None:
    repo = _ProjectionRepo(_batch())

    with pytest.raises(PermissionError, match="another session"):
        ParallelActionPlayerProjectionService(repo).get(
            "parallel-batch-safe",
            _identity(session_id="session-foreign"),
        )


def test_projection_rejects_revoked_participant() -> None:
    repo = _ProjectionRepo(_batch())
    repo.members[OWN_MEMBER_ID]["revoked_at"] = "2026-08-21 10:04:00"

    with pytest.raises(PermissionError, match="active player participant"):
        ParallelActionPlayerProjectionService(repo).get(
            "parallel-batch-safe",
            _identity(),
        )


def test_projection_round_trips_a_real_durable_batch(tmp_path: Path) -> None:
    with db_session(tmp_path / "parallel-player-projection.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _prepared_direct_batch(repo)
        batch = _create_batch(repo, fixture)
        owner = fixture["prepared"][0]["identity"]
        other_action_id = str(fixture["prepared"][1]["action"]["id"])

        payload = ParallelActionPlayerProjectionService(repo).get(
            str(batch["id"]), owner
        ).as_dict()

        ParallelActionPlayerBatchResponse.model_validate(payload)
        assert payload["own_item"]["action_id"] == fixture["prepared"][0][
            "action"
        ]["id"]
        assert other_action_id not in json.dumps(payload, ensure_ascii=False)
