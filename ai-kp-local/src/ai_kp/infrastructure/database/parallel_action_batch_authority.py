"""Authoritative reads and validation for durable parallel action batches."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from ai_kp.infrastructure.database.checks import SkillCheckRepository
from ai_kp.infrastructure.database.parallel_run_fence import ParallelRunFence
from ai_kp.infrastructure.database.rows import decode_json_field
from ai_kp.platform.resolution.check_consequences import (
    build_check_consequence_snapshot,
    exact_kernel_outcome,
)
from ai_kp.platform.resolution.parallel_batch_state import ParallelBatchEvent


class ParallelActionBatchAuthority:
    """Validate batch input against immutable actions and authoritative results.

    This collaborator owns no transaction lifecycle and performs no batch writes.
    The repository supplies the shared SQLite connection and remains responsible
    for locking, compare-and-swap updates, and audit persistence.
    """

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.checks = SkillCheckRepository(connection)
        self.run_fence = ParallelRunFence(connection)

    def decode_batch(self, row: Any) -> dict[str, Any]:
        batch = dict(row)
        item_rows = self.connection.execute(
            """
            SELECT * FROM parallel_action_batch_items
            WHERE batch_id = ? ORDER BY priority DESC, action_id
            """,
            (batch["id"],),
        ).fetchall()
        batch["items"] = []
        for item_row in item_rows:
            item = dict(item_row)
            item["preview"] = self.authoritative_preview(
                proposal_id=str(item["proposal_id"]),
                action_id=str(item["action_id"]),
                run_id=str(batch["run_id"]),
                base_state_version=int(batch["base_state_version"]),
                operator_id=str(item["operator_id"]),
                preview_hash=str(item["preview_hash"]),
            )
            adjudication = self.connection.execute(
                """
                SELECT id, action_id, proposal_id, mode, status, version,
                       selected_skill, skill_options_json, confirmed_at
                FROM player_action_adjudications WHERE id = ?
                """,
                (item["adjudication_id"],),
            ).fetchone()
            if adjudication is None:
                item["adjudication"] = None
            else:
                decoded_adjudication = dict(adjudication)
                decoded_adjudication["skill_options"] = decode_json_field(
                    decoded_adjudication.pop("skill_options_json"), []
                )
                item["adjudication"] = decoded_adjudication
            item["checks"] = self.linked_checks(
                action_id=str(item["action_id"]),
                proposal_id=str(item["proposal_id"]),
            )
            batch["items"].append(item)
        event_rows = self.connection.execute(
            """
            SELECT * FROM parallel_action_batch_events
            WHERE batch_id = ? ORDER BY version, created_at, id
            """,
            (batch["id"],),
        ).fetchall()
        batch["events"] = []
        for event_row in event_rows:
            event = dict(event_row)
            event["payload"] = decode_json_field(event.pop("payload_json"), {})
            batch["events"].append(event)
        return batch

    def validate_scope(
        self,
        *,
        campaign_id: str,
        session_id: str,
        run_id: str,
        module_run_version: int,
        contract_version_id: str,
        base_state_version: int,
        created_by_member_id: str | None,
    ) -> None:
        self.run_fence.validate_scope(
            campaign_id=campaign_id,
            session_id=session_id,
            run_id=run_id,
            module_run_version=module_run_version,
            contract_version_id=contract_version_id,
            base_state_version=base_state_version,
            created_by_member_id=created_by_member_id,
        )

    def validate_execution_authority(self, batch: dict[str, Any]) -> None:
        self.run_fence.validate_execution_authority(batch)

    def execution_authority_error(self, batch: dict[str, Any]) -> str:
        return self.run_fence.execution_authority_error(batch)

    def validate_items(
        self,
        *,
        campaign_id: str,
        session_id: str,
        run_id: str,
        base_state_version: int,
        items: Sequence[dict[str, Any]],
    ) -> None:
        member_ids: set[str] = set()
        for item in items:
            row = self.connection.execute(
                """
                SELECT a.campaign_id, a.session_id, a.member_id, a.pc_id,
                       a.status, a.proposal_id, p.campaign_id AS proposal_campaign_id,
                       p.status AS proposal_status, j.id AS adjudication_id,
                       j.action_id AS adjudication_action_id,
                       j.proposal_id AS adjudication_proposal_id,
                       j.mode AS adjudication_mode,
                       j.status AS adjudication_status,
                       j.selected_skill AS adjudication_selected_skill,
                       j.skill_options_json AS adjudication_skill_options_json
                FROM player_actions a
                JOIN turn_proposals p ON p.id = a.proposal_id
                JOIN player_action_adjudications j ON j.id = ?
                WHERE a.id = ?
                """,
                (item["adjudication_id"], item["action_id"]),
            ).fetchone()
            if (
                row is None
                or row["campaign_id"] != campaign_id
                or row["session_id"] != session_id
                or row["proposal_id"] != item["proposal_id"]
                or row["proposal_campaign_id"] != campaign_id
                or row["status"] != "reviewed"
                or row["proposal_status"] != "draft"
                or row["adjudication_action_id"] != item["action_id"]
                or row["adjudication_proposal_id"] != item["proposal_id"]
                or row["adjudication_status"] not in {"pending", "confirmed"}
            ):
                raise ValueError(
                    "Parallel batch items require distinct reviewed actions and "
                    "draft proposals from one campaign session"
                )
            expected_actor = str(row["pc_id"] or row["member_id"])
            if item["actor_id"] != expected_actor:
                raise ValueError("Parallel batch actor must come from the action owner")
            member_id = str(row["member_id"])
            if member_id in member_ids:
                raise ValueError("One session member can contribute only one batch action")
            member_ids.add(member_id)
            decision_item = {
                **item,
                "preview": self.authoritative_preview(
                    proposal_id=item["proposal_id"],
                    action_id=item["action_id"],
                    run_id=run_id,
                    base_state_version=base_state_version,
                    operator_id=item["operator_id"],
                    preview_hash=item["preview_hash"],
                ),
                "adjudication": {
                    "mode": row["adjudication_mode"],
                    "status": row["adjudication_status"],
                    "selected_skill": row["adjudication_selected_skill"],
                    "skill_options": decode_json_field(
                        row["adjudication_skill_options_json"], []
                    ),
                },
                "checks": self.linked_checks(
                    action_id=item["action_id"],
                    proposal_id=item["proposal_id"],
                ),
            }
            self.validate_item_decision(
                decision_item,
                selected_skill_key=item["selected_skill_key"],
                outcome_key=item["outcome_key"],
                check_result_fingerprint=item["check_result_fingerprint"],
            )

    def authoritative_preview(
        self,
        *,
        proposal_id: str,
        action_id: str,
        run_id: str,
        base_state_version: int,
        operator_id: str,
        preview_hash: str,
    ) -> dict[str, Any]:
        rows = self.connection.execute(
            """
            SELECT payload_json FROM proposal_actions
            WHERE proposal_id = ? AND action_type = 'kernel_resolution'
            ORDER BY created_at, id
            """,
            (proposal_id,),
        ).fetchall()
        if len(rows) != 1:
            raise ValueError(
                "Parallel items require exactly one authoritative kernel preview"
            )
        payload = decode_json_field(rows[0]["payload_json"], {})
        if not isinstance(payload, dict):
            raise TypeError("Parallel kernel preview payload is malformed")
        preview = payload.get("preview")
        if (
            not isinstance(preview, dict)
            or payload.get("run_id") != run_id
            or preview.get("action_id") != action_id
            or preview.get("run_id") != run_id
            or preview.get("run_version") != base_state_version
            or preview.get("operator_id") != operator_id
            or preview.get("preview_hash") != preview_hash
            or preview.get("allowed") is not True
        ):
            raise ValueError(
                "Parallel item does not match its version-bound kernel preview"
            )
        return preview

    def linked_checks(
        self,
        *,
        action_id: str,
        proposal_id: str,
    ) -> list[dict[str, Any]]:
        check_rows = self.connection.execute(
            """
            SELECT id FROM skill_checks
            WHERE player_action_id = ? AND proposal_id = ?
            ORDER BY created_at, id
            """,
            (action_id, proposal_id),
        ).fetchall()
        return [
            self.checks.get_skill_check(str(check["id"]))
            for check in check_rows
        ]

    def assert_actions_not_batched(
        self, items: Sequence[dict[str, Any]]
    ) -> None:
        action_ids = [item["action_id"] for item in items]
        placeholders = ", ".join("?" for _ in action_ids)
        row = self.connection.execute(
            f"""
            SELECT i.action_id, b.id
            FROM parallel_action_batch_items i
            JOIN parallel_action_batches b ON b.id = i.batch_id
            WHERE i.action_id IN ({placeholders})
            LIMIT 1
            """,
            action_ids,
        ).fetchone()
        if row is not None:
            raise ValueError(
                f"Action {row['action_id']} already belongs to batch {row['id']}"
            )

    def validate_batch_actor(
        self,
        batch: dict[str, Any],
        actor_member_id: str | None,
        *,
        require_kp: bool = False,
    ) -> str | None:
        if actor_member_id is None:
            return None
        row = self.connection.execute(
            """
            SELECT campaign_id, session_id, role, revoked_at
            FROM session_members WHERE id = ?
            """,
            (actor_member_id,),
        ).fetchone()
        if (
            row is None
            or row["campaign_id"] != batch["campaign_id"]
            or row["session_id"] != batch["session_id"]
            or row["revoked_at"] is not None
        ):
            raise ValueError("Parallel batch actor must be active in its session")
        role = str(row["role"])
        if require_kp and role != "kp":
            raise PermissionError(
                "Only the KP or deterministic coordinator can transition a batch"
            )
        return role

    def validate_item_actor(
        self,
        batch: dict[str, Any],
        *,
        action_id: str,
        actor_member_id: str | None,
    ) -> str | None:
        role = self.validate_batch_actor(batch, actor_member_id)
        if actor_member_id is None or role == "kp":
            return role
        owner = self.connection.execute(
            "SELECT member_id FROM player_actions WHERE id = ?",
            (action_id,),
        ).fetchone()
        if owner is None or owner["member_id"] != actor_member_id:
            raise PermissionError("Players can update only their own parallel action")
        return role

    def validate_attention_actor(
        self,
        batch: dict[str, Any],
        actor_member_id: str | None,
    ) -> str | None:
        """Allow a batch participant to pause settlement without KP authority."""

        role = self.validate_batch_actor(batch, actor_member_id)
        if actor_member_id is None or role == "kp":
            return role
        owner = self.connection.execute(
            """
            SELECT 1
            FROM parallel_action_batch_items i
            JOIN player_actions a ON a.id = i.action_id
            WHERE i.batch_id = ? AND a.member_id = ?
            LIMIT 1
            """,
            (batch["id"], actor_member_id),
        ).fetchone()
        if owner is None:
            raise PermissionError(
                "Only a parallel action owner can request batch attention"
            )
        return role

    def validate_item_decision(
        self,
        item: dict[str, Any],
        *,
        selected_skill_key: str | None,
        outcome_key: str | None,
        check_result_fingerprint: str | None,
    ) -> None:
        adjudication = item.get("adjudication")
        if adjudication is None:
            raise ValueError("Parallel item lost its authoritative adjudication")
        mode = str(adjudication["mode"])
        if selected_skill_key is not None:
            self._validate_selected_skill(
                item,
                adjudication=adjudication,
                selected_skill_key=selected_skill_key,
            )
        if mode == "skill_check":
            if (outcome_key is None) != (check_result_fingerprint is None):
                raise ValueError(
                    "A checked item binds its exact outcome and fingerprint together"
                )
            if outcome_key is not None:
                expected_fingerprint, expected_outcome = (
                    self.authoritative_check_result(item)
                )
                if (
                    check_result_fingerprint != expected_fingerprint
                    or outcome_key != expected_outcome
                ):
                    raise ValueError(
                        "Parallel item result does not match its authoritative checks"
                    )
        elif mode == "direct_resolution":
            if selected_skill_key is not None or check_result_fingerprint is not None:
                raise ValueError(
                    "Automatic actions cannot bind a skill or check fingerprint"
                )
        else:
            raise ValueError(
                "Clarification adjudications cannot receive deterministic results"
            )

    def _validate_selected_skill(
        self,
        item: dict[str, Any],
        *,
        adjudication: dict[str, Any],
        selected_skill_key: str,
    ) -> None:
        if adjudication["mode"] != "skill_check":
            raise ValueError("Skill selection requires a skill-check adjudication")
        preview = item.get("preview")
        if not isinstance(preview, dict):
            raise ValueError(  # noqa: TRY004 - preserve repository error contract
                "Parallel item lost its authoritative preview"
            )
        selected_name = adjudication.get("selected_skill")
        selected_options = [
            option
            for option in adjudication["skill_options"]
            if isinstance(option, dict)
            and option.get("skill_name") == selected_name
        ]
        matching_choices = [
            choice
            for choice in preview.get("skill_choices") or ()
            if isinstance(choice, dict)
            and choice.get("skill_key") == selected_skill_key
        ]
        if (
            len(selected_options) != 1
            or selected_options[0].get("skill_key") != selected_skill_key
            or len(matching_choices) != 1
        ):
            raise ValueError("Selected skill cannot be mapped to the kernel preview")
        if preview.get("selected_skill_key") != selected_skill_key:
            raise ValueError(
                "Selected skill must match the authoritative adjudication"
            )

    def authoritative_check_result(
        self, item: dict[str, Any]
    ) -> tuple[str, str]:
        opposed_checks = self.checks.list_opposed_checks_for_action(
            str(item["action_id"])
        )
        if opposed_checks:
            # Opposed checks require binding the item to one contest side and
            # interpreting the authoritative winner.  The first parallel-batch
            # workflow deliberately pauses instead of guessing from each side's
            # independent percentile result.
            raise ValueError(
                "Parallel batches require attention for opposed check results"
            )
        snapshot = build_check_consequence_snapshot(item["checks"])
        preview = item.get("preview")
        if not isinstance(preview, dict):
            raise TypeError("Parallel item lost its authoritative preview")
        return (
            str(snapshot["result_fingerprint"]),
            exact_kernel_outcome(preview, item["checks"]),
        )

    def validate_settlement_receipt(
        self,
        batch: dict[str, Any],
        *,
        settlement_hash: str | None,
        scenario_command_batch_id: str | None,
    ) -> None:
        normalized_hash = self.sha256(
            settlement_hash, field_name="settlement_hash"
        )
        if not scenario_command_batch_id:
            raise ValueError("A settled parallel batch requires a command batch receipt")
        receipt = self.connection.execute(
            """
            SELECT run_id, batch_kind, expected_version, preview_hash,
                   preview_json
            FROM scenario_command_batches WHERE id = ?
            """,
            (scenario_command_batch_id,),
        ).fetchone()
        preview_payload = (
            decode_json_field(receipt["preview_json"], None)
            if receipt is not None
            else None
        )
        if (
            receipt is None
            or receipt["run_id"] != batch["run_id"]
            or receipt["batch_kind"] != "parallel"
            or int(receipt["expected_version"]) != batch["base_state_version"]
            or receipt["preview_hash"] != normalized_hash
            or not self._settlement_preview_matches_batch(
                batch,
                preview_payload=preview_payload,
                settlement_hash=normalized_hash,
            )
        ):
            raise ValueError(
                "Settlement receipt does not match the parallel batch authority"
            )

    @classmethod
    def _settlement_preview_matches_batch(
        cls,
        batch: dict[str, Any],
        *,
        preview_payload: Any,
        settlement_hash: str,
    ) -> bool:
        """Bind a committed kernel receipt to every durable batch item.

        ``preview_hash`` on the command-batch row is necessary but insufficient:
        a caller could otherwise attach a valid receipt from another durable batch
        that happened to start at the same run version.  Re-hashing the stored
        preview detects database drift, while the explicit item projection binds
        the kernel receipt to the durable authority record.
        """

        if not isinstance(preview_payload, dict):
            return False
        canonical_payload = dict(preview_payload)
        recorded_hash = canonical_payload.pop("settlement_hash", None)
        if (
            recorded_hash != settlement_hash
            or cls.hash_value(canonical_payload) != settlement_hash
            or preview_payload.get("batch_id") != batch["id"]
            or preview_payload.get("run_id") != batch["run_id"]
            or preview_payload.get("starting_run_version")
            != batch["base_state_version"]
            or preview_payload.get("status") != "ready"
        ):
            return False

        recorded_actions = preview_payload.get("actions")
        if not isinstance(recorded_actions, list):
            return False
        expected_actions = [
            {
                "action_id": item["action_id"],
                "actor_id": item["actor_id"],
                "operator_id": item["operator_id"],
                "priority": item["priority"],
                "outcome": item["outcome_key"],
                "preview_hash": item["preview_hash"],
            }
            for item in batch["items"]
        ]
        actual_actions: list[dict[str, Any]] = []
        for action in recorded_actions:
            if not isinstance(action, dict):
                return False
            action_preview = action.get("preview")
            if (
                not isinstance(action_preview, dict)
                or type(action.get("priority")) is not int
            ):
                return False
            actual_actions.append(
                {
                    "action_id": action.get("action_id"),
                    "actor_id": action.get("actor_id"),
                    "operator_id": action_preview.get("operator_id"),
                    "priority": action.get("priority"),
                    "outcome": action.get("outcome"),
                    "preview_hash": action_preview.get("preview_hash"),
                }
            )
        return actual_actions == expected_actions

    def validate_transition(
        self,
        batch: dict[str, Any],
        event: ParallelBatchEvent,
    ) -> None:
        if event == "resume_confirmations":
            self._assert_recoverable_confirmations(batch)
            return
        if event == "resume_checks":
            self._assert_confirmed_items(batch, require_checks=True)
            self._assert_direct_outcomes_present(batch)
            self._assert_recoverable_checks(batch)
            return
        if event == "confirmations_completed_with_checks":
            self._assert_confirmed_items(batch, require_checks=True)
            return
        if event == "confirmations_completed_without_checks":
            self._assert_confirmed_items(batch, require_checks=False)
            self._assert_item_outcomes_present(batch)
            return
        if event in {
            "checks_completed",
            "begin_commit",
            "commit_succeeded",
            "resume_ready",
        }:
            self._assert_authoritative_results(batch)

    def _assert_recoverable_confirmations(self, batch: dict[str, Any]) -> None:
        pending = False
        for item in batch["items"]:
            adjudication = item.get("adjudication") or {}
            status = adjudication.get("status")
            if status not in {"pending", "confirmed"}:
                raise ValueError(
                    "Parallel adjudications are not recoverable for confirmation"
                )
            pending = pending or status == "pending"
            if (
                item.get("checks")
                or item.get("outcome_key") is not None
                or item.get("check_result_fingerprint") is not None
            ):
                raise ValueError(
                    "Confirmation recovery cannot retain pre-existing results"
                )
            row = self.connection.execute(
                """
                SELECT a.status AS action_status, a.proposal_id,
                       p.status AS proposal_status
                FROM player_actions a
                JOIN turn_proposals p ON p.id = a.proposal_id
                WHERE a.id = ?
                """,
                (item["action_id"],),
            ).fetchone()
            if (
                row is None
                or row["action_status"] != "reviewed"
                or row["proposal_id"] != item["proposal_id"]
                or row["proposal_status"] != "draft"
            ):
                raise ValueError(
                    "Confirmation recovery requires reviewed actions and draft proposals"
                )
        if not pending:
            raise ValueError(
                "A fully confirmed parallel batch cannot resume confirmations"
            )

    def _assert_recoverable_checks(self, batch: dict[str, Any]) -> None:
        for item in batch["items"]:
            if item["adjudication"]["mode"] != "skill_check":
                continue
            if any(check["status"] == "cancelled" for check in item["checks"]):
                raise ValueError("Cancelled parallel checks cannot be resumed")
            if self.checks.list_opposed_checks_for_action(str(item["action_id"])):
                raise ValueError("Opposed parallel checks cannot be resumed")

    def _assert_confirmed_items(
        self,
        batch: dict[str, Any],
        *,
        require_checks: bool,
    ) -> None:
        checked_items = []
        for item in batch["items"]:
            adjudication = item.get("adjudication")
            if adjudication is None or adjudication.get("status") != "confirmed":
                raise ValueError(
                    "All parallel action adjudications require player confirmation"
                )
            mode = adjudication.get("mode")
            if mode == "roleplay_or_clarification":
                raise ValueError(
                    "Clarification adjudications cannot enter deterministic settlement"
                )
            if mode == "skill_check":
                selected_skill_key = item.get("selected_skill_key")
                if not isinstance(selected_skill_key, str) or not selected_skill_key:
                    raise ValueError(
                        "Confirmed skill-check items require a persisted skill key"
                    )
                self._validate_selected_skill(
                    item,
                    adjudication=adjudication,
                    selected_skill_key=selected_skill_key,
                )
                checked_items.append(item)
            elif mode != "direct_resolution":
                raise ValueError("Parallel action adjudication mode is unsupported")

        if require_checks:
            if not checked_items:
                raise ValueError("This parallel batch has no confirmed skill checks")
            if any(not item["checks"] for item in checked_items):
                raise ValueError(
                    "Every skill-check adjudication requires its authoritative check"
                )
        elif checked_items or any(item["checks"] for item in batch["items"]):
            raise ValueError(
                "A batch with skill checks cannot skip the check-results phase"
            )

    def _assert_authoritative_results(self, batch: dict[str, Any]) -> None:
        has_checks = any(
            (item.get("adjudication") or {}).get("mode") == "skill_check"
            for item in batch["items"]
        )
        self._assert_confirmed_items(batch, require_checks=has_checks)
        for item in batch["items"]:
            if item["adjudication"]["mode"] != "skill_check":
                if item["outcome_key"] is None:
                    raise ValueError(
                        "Every automatic parallel action requires an exact outcome"
                    )
                continue
            fingerprint, exact_outcome = self.authoritative_check_result(item)
            if item["check_result_fingerprint"] != fingerprint:
                raise ValueError(
                    "Parallel check fingerprint is missing or no longer authoritative"
                )
            if item["outcome_key"] != exact_outcome:
                raise ValueError(
                    "Parallel item outcome does not match its authoritative checks"
                )

    @staticmethod
    def _assert_item_outcomes_present(batch: dict[str, Any]) -> None:
        if any(item["outcome_key"] is None for item in batch["items"]):
            raise ValueError(
                "Every automatic parallel action requires an exact outcome"
            )

    @staticmethod
    def _assert_direct_outcomes_present(batch: dict[str, Any]) -> None:
        if any(
            item["adjudication"]["mode"] == "direct_resolution"
            and item["outcome_key"] is None
            for item in batch["items"]
        ):
            raise ValueError(
                "Every confirmed direct parallel action requires an exact outcome"
            )

    @classmethod
    def normalize_items(
        cls, items: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for raw in items:
            priority = raw.get("priority", 0)
            if type(priority) is not int or not -1000 <= priority <= 1000:
                raise ValueError("Parallel item priority must be between -1000 and 1000")
            normalized.append(
                {
                    "action_id": cls.text(
                        raw.get("action_id"), field_name="action_id", maximum=160
                    ),
                    "proposal_id": cls.text(
                        raw.get("proposal_id"), field_name="proposal_id", maximum=160
                    ),
                    "adjudication_id": cls.text(
                        raw.get("adjudication_id"),
                        field_name="adjudication_id",
                        maximum=160,
                    ),
                    "actor_id": cls.text(
                        raw.get("actor_id"), field_name="actor_id", maximum=160
                    ),
                    "operator_id": cls.text(
                        raw.get("operator_id"), field_name="operator_id", maximum=160
                    ),
                    "preview_hash": cls.sha256(
                        raw.get("preview_hash"), field_name="preview_hash"
                    ),
                    "selected_skill_key": cls.optional_text(
                        raw.get("selected_skill_key"),
                        field_name="selected_skill_key",
                        maximum=120,
                    ),
                    "outcome_key": cls.optional_text(
                        raw.get("outcome_key"),
                        field_name="outcome_key",
                        maximum=120,
                    ),
                    "check_result_fingerprint": (
                        cls.sha256(
                            raw.get("check_result_fingerprint"),
                            field_name="check_result_fingerprint",
                        )
                        if raw.get("check_result_fingerprint") is not None
                        else None
                    ),
                    "priority": priority,
                }
            )
        action_ids = [item["action_id"] for item in normalized]
        proposal_ids = [item["proposal_id"] for item in normalized]
        adjudication_ids = [item["adjudication_id"] for item in normalized]
        actor_ids = [item["actor_id"] for item in normalized]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("Parallel batch action IDs must be unique")
        if len(proposal_ids) != len(set(proposal_ids)):
            raise ValueError("Parallel batch proposal IDs must be unique")
        if len(adjudication_ids) != len(set(adjudication_ids)):
            raise ValueError("Parallel batch adjudication IDs must be unique")
        if len(actor_ids) != len(set(actor_ids)):
            raise ValueError("Parallel batch actor IDs must be unique")
        return sorted(normalized, key=lambda item: (-item["priority"], item["action_id"]))

    @classmethod
    def preparation_hash(
        cls,
        *,
        campaign_id: str,
        session_id: str,
        run_id: str,
        module_run_version: int,
        contract_version_id: str,
        base_state_version: int,
        items: Sequence[dict[str, Any]],
    ) -> str:
        return cls.hash_value(
            {
                "campaign_id": campaign_id,
                "session_id": session_id,
                "run_id": run_id,
                "module_run_version": module_run_version,
                "contract_version_id": contract_version_id,
                "base_state_version": base_state_version,
                "items": list(items),
            }
        )

    @staticmethod
    def hash_value(value: Any) -> str:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def sha256(cls, value: Any, *, field_name: str) -> str:
        normalized = cls.text(value, field_name=field_name, maximum=64)
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError(f"{field_name} must be a lowercase SHA-256 hash")
        return normalized

    @staticmethod
    def text(value: Any, *, field_name: str, maximum: int) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} is required")
        normalized = value.strip()
        if len(normalized) > maximum:
            raise ValueError(f"{field_name} is too long")
        return normalized

    @classmethod
    def optional_text(
        cls, value: Any, *, field_name: str, maximum: int
    ) -> str | None:
        return (
            None
            if value is None
            else cls.text(value, field_name=field_name, maximum=maximum)
        )


__all__ = ["ParallelActionBatchAuthority"]
