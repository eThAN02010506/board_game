from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ai_kp.application.action_adjudication_service import (
    ActionAdjudicationService,
)
from ai_kp.application.kernel_action_service import KernelActionService
from ai_kp.application.parallel_kernel_service import ParallelKernelService
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.infrastructure.database.parallel_action_batch_authority import (
    ParallelActionBatchAuthority,
)
from ai_kp.platform.resolution.parallel import (
    ParallelIntent,
    ParallelSettlementRequest,
)
from tests.test_check_consequence_fingerprint import _check
from tests.test_parallel_kernel_service import setup_bound_run


def _prepared_direct_batch(repo: Repository) -> dict[str, Any]:
    run = setup_bound_run(repo)
    sessions = SessionService(repo)
    session = sessions.create(str(run["campaign_id"]))
    first_bundle = sessions.join(session["join_code"], display_name="Ada")
    second_bundle = sessions.join(session["join_code"], display_name="Bert")
    kp = repo.authenticate_access_token(session["access_token"])
    first = repo.authenticate_access_token(first_bundle["access_token"])
    second = repo.authenticate_access_token(second_bundle["access_token"])
    assert kp is not None and first is not None and second is not None
    state = ParallelKernelService(repo).initialize_run(str(run["id"]))
    binding = repo.get_module_run_contract_binding(str(run["id"]))

    prepared = []
    for index, (identity, operator_id) in enumerate(
        ((first, "open-panel"), (second, "observe-quietly")), start=1
    ):
        action = TurnService(repo).submit_player_action(
            identity,
            action_text=f"parallel action {index}",
            client_action_id=f"parallel-fixture-{index}",
        )
        proposal, preview = KernelActionService(repo).prepare_manual(
            action,
            identity,
            operator_id=operator_id,
            requested_skill_key=None,
        )
        adjudication = ActionAdjudicationService(repo).create(
            action,
            proposal,
            source_model="parallel-fixture",
            enforce_precheck=False,
        )
        prepared.append(
            {
                "identity": identity,
                "action": action,
                "proposal": proposal,
                "preview": preview,
                "adjudication": adjudication,
                "item": {
                    "action_id": action["id"],
                    "proposal_id": proposal["id"],
                    "adjudication_id": adjudication["id"],
                    "actor_id": str(action.get("pc_id") or action["member_id"]),
                    "operator_id": operator_id,
                    "preview_hash": preview.preview_hash,
                    "outcome_key": "success",
                    "priority": 3 - index,
                },
            }
        )

    return {
        "run": run,
        "session": session,
        "kp": kp,
        "prepared": prepared,
        "state": state,
        "binding": binding,
    }


def _create_batch(
    repo: Repository,
    fixture: dict[str, Any],
    *,
    idempotency_key: str = "parallel:durable-fixture",
    created_by_member_id: str | None = None,
) -> dict[str, Any]:
    return repo.create_parallel_action_batch(
        campaign_id=str(fixture["run"]["campaign_id"]),
        session_id=str(fixture["session"]["session"]["id"]),
        run_id=str(fixture["run"]["id"]),
        module_run_version=int(
            repo.get_campaign_module_run(str(fixture["run"]["id"]))["version"]
        ),
        contract_version_id=str(fixture["binding"]["contract_version_id"]),
        base_state_version=int(fixture["state"]["state_version"]),
        idempotency_key=idempotency_key,
        items=[entry["item"] for entry in fixture["prepared"]],
        created_by_member_id=(
            fixture["kp"].member_id
            if created_by_member_id is None
            else created_by_member_id
        ),
    )


def _confirm_adjudications(repo: Repository, fixture: dict[str, Any]) -> None:
    for entry in fixture["prepared"]:
        repo.confirm_action_adjudication(
            str(entry["action"]["id"]),
            expected_version=int(entry["adjudication"]["version"]),
            actor_member_id=entry["identity"].member_id,
        )


def test_parallel_repository_preserves_an_authorized_critical_outcome(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-exact-check.sqlite3") as connection:
        repo = Repository(connection)
        check = _check(success_level="critical", selected_roll=1)
        fingerprint, outcome = repo._authoritative_check_result(
            {
                "action_id": "action-1",
                "checks": [check],
                "preview": {
                    "outcome_branches": [
                        {"outcome_key": "critical", "commands": [{}]}
                    ]
                },
            }
        )

        assert len(fingerprint) == 64
        assert outcome == "critical"


def test_parallel_skill_authority_uses_canonical_keys_and_persists_selection(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-skill-authority.sqlite3") as connection:
        authority = ParallelActionBatchAuthority(connection)
        item = {
            "action_id": "action-skill",
            "selected_skill_key": "coc7.spot_hidden",
            "adjudication": {
                "mode": "skill_check",
                "status": "confirmed",
                "selected_skill": "侦查",
                "skill_options": [{
                    "skill_name": "侦查",
                    "skill_key": "coc7.spot_hidden",
                    "reason": "adjudication wording may differ",
                }],
            },
            "preview": {
                "selected_skill_key": "coc7.spot_hidden",
                "skill_choices": [{
                    "skill_key": "coc7.spot_hidden",
                    "reason": "kernel wording is independently authored",
                }],
            },
            "checks": [{}],
        }

        authority.validate_item_decision(
            item,
            selected_skill_key="coc7.spot_hidden",
            outcome_key=None,
            check_result_fingerprint=None,
        )
        authority.validate_transition(
            {"items": [item]}, "confirmations_completed_with_checks"
        )

        missing_selection = {**item, "selected_skill_key": None}
        with pytest.raises(ValueError, match="persisted skill key"):
            authority.validate_transition(
                {"items": [missing_selection]},
                "confirmations_completed_with_checks",
            )

        mismatched_option = json.loads(json.dumps(item))
        mismatched_option["adjudication"]["skill_options"][0][
            "skill_key"
        ] = "coc7.listen"
        mismatched_option["adjudication"]["skill_options"][0][
            "reason"
        ] = mismatched_option["preview"]["skill_choices"][0]["reason"]
        with pytest.raises(ValueError, match="mapped to the kernel preview"):
            authority.validate_item_decision(
                mismatched_option,
                selected_skill_key="coc7.spot_hidden",
                outcome_key=None,
                check_result_fingerprint=None,
            )

        mismatched_preview_selection = json.loads(json.dumps(item))
        mismatched_preview_selection["preview"]["selected_skill_key"] = (
            "coc7.listen"
        )
        with pytest.raises(ValueError, match="authoritative adjudication"):
            authority.validate_item_decision(
                mismatched_preview_selection,
                selected_skill_key="coc7.spot_hidden",
                outcome_key=None,
                check_result_fingerprint=None,
            )


def test_parallel_authority_rejects_opposed_results_until_side_binding_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with db_session(tmp_path / "parallel-opposed-attention.sqlite3") as connection:
        authority = ParallelActionBatchAuthority(connection)
        monkeypatch.setattr(
            authority.checks,
            "list_opposed_checks_for_action",
            lambda _action_id: [{"id": "opposed-1", "status": "resolved"}],
        )

        with pytest.raises(ValueError, match="require attention for opposed"):
            authority.authoritative_check_result(
                {
                    "action_id": "action-1",
                    "checks": [_check()],
                    "preview": {"outcome_branches": []},
                }
            )


def test_parallel_batch_persists_authoritative_items_and_replays_exactly(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-durable.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _prepared_direct_batch(repo)
        with pytest.raises(PermissionError, match="active session KP"):
            _create_batch(
                repo,
                fixture,
                idempotency_key="parallel:player-cannot-create",
                created_by_member_id=fixture["prepared"][0]["identity"].member_id,
            )
        batch = _create_batch(repo, fixture)
        replay = _create_batch(repo, fixture)

        assert replay["id"] == batch["id"]
        assert batch["status"] == "awaiting_confirmation"
        assert batch["version"] == 1
        assert [item["operator_id"] for item in batch["items"]] == [
            "open-panel",
            "observe-quietly",
        ]
        assert all(item["adjudication"]["status"] == "pending" for item in batch["items"])
        assert all(item["preview"]["preview_hash"] == item["preview_hash"] for item in batch["items"])
        assert batch["events"][0]["event_type"] == "created"

        changed = dict(fixture)
        changed["prepared"] = [dict(entry) for entry in fixture["prepared"]]
        changed["prepared"][0]["item"] = {
            **fixture["prepared"][0]["item"],
            "outcome_key": "failure",
        }
        with pytest.raises(ValueError, match="different parallel action batch"):
            _create_batch(repo, changed)


def test_parallel_item_updates_enforce_owner_and_optimistic_version(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-owner.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _prepared_direct_batch(repo)
        batch = _create_batch(repo, fixture)
        first_entry, second_entry = fixture["prepared"]

        with pytest.raises(PermissionError, match="only their own"):
            repo.record_parallel_action_item_decision(
                batch["id"],
                first_entry["action"]["id"],
                expected_version=1,
                selected_skill_key="not-authorized",
                actor_member_id=second_entry["identity"].member_id,
            )

        updated = repo.record_parallel_action_item_decision(
            batch["id"],
            first_entry["action"]["id"],
            expected_version=1,
            outcome_key="success",
            actor_member_id=fixture["kp"].member_id,
        )
        assert updated["version"] == 2
        with pytest.raises(ValueError, match="changed; refresh"):
            repo.record_parallel_action_item_decision(
                batch["id"],
                first_entry["action"]["id"],
                expected_version=1,
                outcome_key="success",
                actor_member_id=fixture["kp"].member_id,
            )
        assert repo.get_parallel_action_batch(batch["id"])["version"] == 2


def test_parallel_batch_transitions_and_settlement_receipt_are_fail_closed(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-transitions.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _prepared_direct_batch(repo)
        batch = _create_batch(repo, fixture)

        with pytest.raises(ValueError, match="Cannot begin_commit"):
            repo.transition_parallel_action_batch(
                batch["id"],
                "begin_commit",
                expected_version=1,
                actor_member_id=fixture["kp"].member_id,
            )
        with pytest.raises(ValueError, match="require player confirmation"):
            repo.transition_parallel_action_batch(
                batch["id"],
                "confirmations_completed_without_checks",
                expected_version=1,
                actor_member_id=fixture["kp"].member_id,
            )

        _confirm_adjudications(repo, fixture)
        ready = repo.transition_parallel_action_batch(
            batch["id"],
            "confirmations_completed_without_checks",
            expected_version=1,
            actor_member_id=fixture["kp"].member_id,
        )
        assert ready["status"] == "ready"
        with pytest.raises(ValueError, match="changed; refresh"):
            repo.transition_parallel_action_batch(
                batch["id"],
                "begin_commit",
                expected_version=1,
                actor_member_id=fixture["kp"].member_id,
            )
        committing = repo.transition_parallel_action_batch(
            batch["id"],
            "begin_commit",
            expected_version=2,
            actor_member_id=fixture["kp"].member_id,
        )

        request = ParallelSettlementRequest(
            batch_id=batch["id"],
            intents=tuple(
                ParallelIntent(
                    action_id=str(entry["action"]["id"]),
                    actor_id=str(entry["item"]["actor_id"]),
                    operator_id=str(entry["item"]["operator_id"]),
                    outcome="success",
                    priority=int(entry["item"]["priority"]),
                )
                for entry in fixture["prepared"]
            ),
        )
        result = ParallelKernelService(repo).settle(
            str(fixture["run"]["id"]),
            request,
            idempotency_key="parallel:durable-settlement",
        )
        assert result.batch is not None

        receipt_id = str(result.batch["id"])
        receipt_row = connection.execute(
            """
            SELECT preview_hash, preview_json FROM scenario_command_batches
            WHERE id = ?
            """,
            (receipt_id,),
        ).fetchone()
        assert receipt_row is not None
        original_hash = str(receipt_row["preview_hash"])
        original_preview_json = str(receipt_row["preview_json"])

        for field_name in (
            "batch_id",
            "action_id",
            "actor_id",
            "operator_id",
            "priority",
            "outcome",
            "preview_hash",
        ):
            payload = json.loads(original_preview_json)
            first_action = payload["actions"][0]
            if field_name == "batch_id":
                payload["batch_id"] = "parallelbatch_other"
            elif field_name in {"action_id", "actor_id"}:
                first_action[field_name] = f"tampered-{field_name}"
            elif field_name == "operator_id":
                first_action["preview"][field_name] = "tampered-operator"
            elif field_name == "priority":
                first_action[field_name] += 1
            elif field_name == "outcome":
                first_action[field_name] = "failure"
            else:
                first_action["preview"][field_name] = "0" * 64
            canonical = dict(payload)
            canonical.pop("settlement_hash", None)
            tampered_hash = hashlib.sha256(
                json.dumps(
                    canonical,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            payload["settlement_hash"] = tampered_hash
            connection.execute(
                """
                UPDATE scenario_command_batches
                SET preview_hash = ?, preview_json = ? WHERE id = ?
                """,
                (
                    tampered_hash,
                    json.dumps(payload, ensure_ascii=False),
                    receipt_id,
                ),
            )
            with pytest.raises(ValueError, match="receipt does not match"):
                repo.transition_parallel_action_batch(
                    batch["id"],
                    "commit_succeeded",
                    expected_version=int(committing["version"]),
                    actor_member_id=fixture["kp"].member_id,
                    settlement_hash=tampered_hash,
                    scenario_command_batch_id=receipt_id,
                )

        connection.execute(
            """
            UPDATE scenario_command_batches
            SET preview_hash = ?, preview_json = ? WHERE id = ?
            """,
            (original_hash, original_preview_json, receipt_id),
        )
        settled = repo.transition_parallel_action_batch(
            batch["id"],
            "commit_succeeded",
            expected_version=int(committing["version"]),
            actor_member_id=fixture["kp"].member_id,
            settlement_hash=result.preview.settlement_hash,
            scenario_command_batch_id=receipt_id,
        )

        assert settled["status"] == "settled"
        assert settled["settlement_hash"] == result.preview.settlement_hash
        assert repo.get_active_parallel_action_batch_for_action(
            str(fixture["prepared"][0]["action"]["id"])
        ) is None
        assert [event["version"] for event in settled["events"]] == [1, 2, 3, 4]
