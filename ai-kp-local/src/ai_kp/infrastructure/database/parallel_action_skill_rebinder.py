"""Atomic authority rebinding for a parallel action's selected skill."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any, Protocol

from ai_kp.infrastructure.database.parallel_action_batch_authority import (
    ParallelActionBatchAuthority,
)
from ai_kp.platform.resolution.contracts import ResolutionPreview
from ai_kp.platform.resolution.narrative_adapter import KernelNarrativeBundle
from ai_kp.platform.resolution.selected_action import (
    SelectedOperator,
    prepare_selected_kernel_action,
)


class _ParallelActionSkillHost(Protocol):
    """Only the composed repository capabilities needed during a skill rebind."""

    connection: sqlite3.Connection

    def begin_immediate(self) -> None: ...

    def get_parallel_action_batch(self, batch_id: str) -> dict[str, Any]: ...

    def get_player_action(self, action_id: str) -> dict[str, Any]: ...

    def get_module_run_contract_binding(self, run_id: str) -> dict[str, Any]: ...

    def get_scenario_run_state(self, run_id: str) -> dict[str, Any]: ...

    def get_action_adjudication(self, action_id: str) -> dict[str, Any]: ...

    def replace_adjudication_skill(
        self,
        action_id: str,
        *,
        expected_version: int,
        skill_name: str,
        actor_member_id: str,
    ) -> dict[str, Any]: ...

    def _item_for_decision(
        self,
        batch: dict[str, Any],
        *,
        action_id: str,
        selected_skill_key: str | None,
        check_result_fingerprint: str | None,
    ) -> dict[str, Any]: ...

    def _append_parallel_batch_event(
        self,
        batch_id: str,
        *,
        version: int,
        event_type: str,
        actor_member_id: str | None,
        payload: dict[str, Any],
    ) -> None: ...


class ParallelActionSkillRebinder:
    """Move all durable authority surfaces to one canonical skill decision."""

    _SAVEPOINT = "rebind_parallel_action_item_skill"

    def __init__(
        self,
        connection: sqlite3.Connection,
        host: _ParallelActionSkillHost,
    ) -> None:
        if host.connection is not connection:
            raise ValueError("Parallel skill rebinding requires one shared connection")
        self.connection = connection
        self.host = host
        self.authority = ParallelActionBatchAuthority(connection)

    def rebind(
        self,
        batch_id: str,
        action_id: str,
        *,
        expected_batch_version: int,
        expected_adjudication_version: int,
        selected_skill_key: str,
        preview: Mapping[str, Any],
        narrative: Mapping[str, Any],
        actor_member_id: str,
    ) -> dict[str, Any]:
        normalized_skill = self.authority.text(
            selected_skill_key,
            field_name="selected_skill_key",
            maximum=120,
        )
        normalized_preview = ResolutionPreview.model_validate(preview)
        normalized_narrative = KernelNarrativeBundle.model_validate(narrative)
        preview_hash = self.authority.sha256(
            normalized_preview.preview_hash,
            field_name="preview_hash",
        )

        self.host.begin_immediate()
        self.connection.execute(f"SAVEPOINT {self._SAVEPOINT}")
        try:
            batch = self.host.get_parallel_action_batch(batch_id)
            self.authority.validate_execution_authority(batch)
            item = self.host._item_for_decision(
                batch,
                action_id=action_id,
                selected_skill_key=normalized_skill,
                check_result_fingerprint=None,
            )
            self.authority.validate_item_actor(
                batch,
                action_id=action_id,
                actor_member_id=actor_member_id,
            )
            if batch["version"] != expected_batch_version:
                raise ValueError(
                    "Parallel action batch changed; refresh before choosing a skill"
                )
            action = self.host.get_player_action(action_id)
            binding = self.host.get_module_run_contract_binding(str(batch["run_id"]))
            state = self.host.get_scenario_run_state(str(batch["run_id"]))
            if (
                str(binding["contract_version_id"])
                != batch["contract_version_id"]
                or str(state["contract_version_id"])
                != batch["contract_version_id"]
                or int(state["state_version"]) != batch["base_state_version"]
                or action.get("proposal_id") != item["proposal_id"]
                or action.get("status") != "reviewed"
            ):
                raise ValueError(
                    "Scenario or action authority changed before skill replacement"
                )
            expected = prepare_selected_kernel_action(
                binding["contract"],
                state["snapshot"],
                action_id=action_id,
                actor_id=str(item["actor_id"]),
                player_action=str(action["action_text"]),
                selection=SelectedOperator(
                    operator_id=str(item["operator_id"]),
                    requested_skill_key=normalized_skill,
                ),
            )
            expected_preview = expected.preview
            if normalized_preview != expected_preview or not expected_preview.allowed:
                raise ValueError(
                    "Replacement preview is outside the durable batch authority"
                )
            matching_choices = [
                choice
                for choice in normalized_preview.skill_choices
                if choice.skill_key == normalized_skill
            ]
            if len(matching_choices) != 1:
                raise ValueError(
                    "Replacement skill is not authorized by the kernel preview"
                )
            expected_narrative = expected.deterministic_narrative
            if normalized_narrative != expected_narrative:
                raise ValueError(
                    "Replacement narrative does not match its deterministic preview"
                )

            adjudication = self.host.get_action_adjudication(action_id)
            if (
                adjudication["id"] != item["adjudication_id"]
                or adjudication["status"] != "pending"
                or adjudication["mode"] != "skill_check"
                or adjudication["version"] != expected_adjudication_version
            ):
                raise ValueError(
                    "Parallel adjudication changed; refresh before choosing a skill"
                )
            selected_options = [
                option
                for option in adjudication["skill_options"]
                if isinstance(option, dict)
                and option.get("skill_key") == normalized_skill
            ]
            if len(selected_options) != 1:
                raise ValueError(
                    "Selected skill is not an approved character-sheet option"
                )
            selected_name = str(selected_options[0]["skill_name"])
            if adjudication.get("selected_skill") != selected_name:
                adjudication = self.host.replace_adjudication_skill(
                    action_id,
                    expected_version=expected_adjudication_version,
                    skill_name=selected_name,
                    actor_member_id=actor_member_id,
                )

            payload_rows = self.connection.execute(
                """
                SELECT id, payload_json FROM proposal_actions
                WHERE proposal_id = ? AND action_type = 'kernel_resolution'
                ORDER BY created_at, id
                """,
                (item["proposal_id"],),
            ).fetchall()
            if len(payload_rows) != 1:
                raise ValueError(
                    "Parallel skill replacement requires one kernel resolution"
                )
            payload = json.loads(str(payload_rows[0]["payload_json"]))
            if not isinstance(payload, dict):
                raise TypeError("Parallel kernel resolution payload is malformed")
            selection = payload.get("selection")
            selected = (
                selection.get("selection") if isinstance(selection, dict) else None
            )
            if (
                not isinstance(selected, dict)
                or selected.get("kind") != "operator"
                or selected.get("candidate_id") != item["operator_id"]
            ):
                raise ValueError(
                    "Parallel skill replacement lost its semantic operator binding"
                )
            payload["selection"] = {
                **selection,
                "selection": {
                    **selected,
                    "requested_skill_key": normalized_skill,
                },
            }
            payload["preview"] = normalized_preview.model_dump(mode="json")
            payload["narrative"] = normalized_narrative.model_dump(mode="json")
            updated_payload = self.connection.execute(
                "UPDATE proposal_actions SET payload_json = ? WHERE id = ?",
                (
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    payload_rows[0]["id"],
                ),
            )
            if updated_payload.rowcount != 1:
                raise ValueError("Parallel kernel preview changed during replacement")
            updated_proposal = self.connection.execute(
                """
                UPDATE turn_proposals
                SET kp_notes = ?, public_narration = ?
                WHERE id = ? AND status = 'draft'
                """,
                (
                    f"operator={item['operator_id']}; hash={preview_hash}",
                    normalized_narrative.preview_narration,
                    item["proposal_id"],
                ),
            )
            if updated_proposal.rowcount != 1:
                raise ValueError("Only a draft parallel proposal can change skill")
            updated_batch = self.connection.execute(
                """
                UPDATE parallel_action_batches
                SET version = version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND version = ? AND status = 'awaiting_confirmation'
                """,
                (batch_id, expected_batch_version),
            )
            if updated_batch.rowcount != 1:
                raise ValueError(
                    "Parallel action batch changed; refresh before choosing a skill"
                )
            updated_item = self.connection.execute(
                """
                UPDATE parallel_action_batch_items
                SET selected_skill_key = ?, preview_hash = ?
                WHERE batch_id = ? AND action_id = ?
                """,
                (normalized_skill, preview_hash, batch_id, action_id),
            )
            if updated_item.rowcount != 1:
                raise KeyError(f"Parallel batch action not found: {action_id}")
            self.host._append_parallel_batch_event(
                batch_id,
                version=expected_batch_version + 1,
                event_type="item_updated",
                actor_member_id=actor_member_id,
                payload={
                    "action_id": action_id,
                    "selected_skill_key": normalized_skill,
                    "preview_hash": preview_hash,
                    "authority_rebound": True,
                },
            )
            rebound = self.host.get_parallel_action_batch(batch_id)
            rebound_item = next(
                entry
                for entry in rebound["items"]
                if entry["action_id"] == action_id
            )
            self.authority.validate_item_decision(
                rebound_item,
                selected_skill_key=normalized_skill,
                outcome_key=None,
                check_result_fingerprint=None,
            )
            self.connection.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
        except Exception:
            self.connection.execute(f"ROLLBACK TO SAVEPOINT {self._SAVEPOINT}")
            self.connection.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
            raise
        return {
            "batch": rebound,
            "adjudication": adjudication,
        }


__all__ = ["ParallelActionSkillRebinder"]
