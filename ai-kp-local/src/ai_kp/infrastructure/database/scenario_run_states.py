"""Evented authoritative scenario snapshots with optimistic command commits."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.resolution.authority_basis import KernelAuthorityBasis
from ai_kp.platform.resolution.contracts import (
    ResolutionPreview,
    ScenarioContract,
    ScenarioSnapshot,
    WorldCommand,
)
from ai_kp.platform.resolution.parallel import ParallelSettlementPreview
from ai_kp.platform.resolution.settlement import resolved_action_commands
from ai_kp.platform.resolution.world_settlement import settle_world_commands


class ScenarioRunStateRepository(SQLiteRepository):
    def begin_scenario_command_batch(self) -> None:
        self.connection.execute("SAVEPOINT scenario_command_batch")

    def finish_scenario_command_batch(self) -> None:
        self.connection.execute("RELEASE SAVEPOINT scenario_command_batch")

    def rollback_scenario_command_batch(self) -> None:
        self.connection.execute("ROLLBACK TO SAVEPOINT scenario_command_batch")
        self.connection.execute("RELEASE SAVEPOINT scenario_command_batch")

    def initialize_scenario_run_state(
        self, run_id: str, *, actor_locations: dict[str, str] | None = None
    ) -> dict[str, Any]:
        existing = self._find_scenario_run_state(run_id)
        if existing is not None:
            return existing
        binding = self.get_module_run_contract_binding(run_id)  # type: ignore[attr-defined]
        snapshot = binding["contract"].initial_snapshot(
            run_id, actor_locations=actor_locations
        )
        self.connection.execute(
            """
            INSERT OR IGNORE INTO scenario_run_states
              (run_id, contract_version_id, state_version, snapshot_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                run_id,
                binding["contract_version_id"],
                snapshot.run_version,
                json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False),
            ),
        )
        return self.get_scenario_run_state(run_id)

    def get_scenario_run_state(self, run_id: str) -> dict[str, Any]:
        result = self._find_scenario_run_state(run_id)
        if result is None:
            raise KeyError(f"Scenario run state not found: {run_id}")
        return result

    def _find_scenario_run_state(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM scenario_run_states WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["snapshot"] = ScenarioSnapshot.model_validate(
            decode_json_field(result.pop("snapshot_json"), {})
        )
        return result

    def get_scenario_command_batch_by_key(
        self, run_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM scenario_command_batches
            WHERE run_id = ? AND idempotency_key = ?
            """,
            (run_id, idempotency_key),
        ).fetchone()
        return self._decode_batch(row) if row is not None else None

    def list_scenario_command_batches(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM scenario_command_batches
            WHERE run_id = ? ORDER BY result_version
            """,
            (run_id,),
        ).fetchall()
        return [self._decode_batch(row) for row in rows]

    def list_recent_scenario_command_batches(
        self, run_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, 100))
        rows = self.connection.execute(
            """
            SELECT * FROM scenario_command_batches
            WHERE run_id = ?
            ORDER BY result_version DESC, created_at DESC
            LIMIT ?
            """,
            (run_id, bounded_limit),
        ).fetchall()
        return [self._decode_batch(row) for row in reversed(rows)]

    def commit_parallel_scenario_batch(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        preview: ParallelSettlementPreview,
        authority_basis: KernelAuthorityBasis,
    ) -> dict[str, Any]:
        if preview.status != "ready":
            raise ValueError("Only a ready parallel settlement can be committed")
        authority_basis.require_artifact(
            run_id=preview.run_id,
            state_version=preview.starting_run_version,
            preview_hash=preview.settlement_hash,
        )
        if preview.run_id != run_id:
            raise ValueError("Parallel settlement belongs to a different run")
        if preview.resulting_snapshot.run_version != preview.starting_run_version + 1:
            raise ValueError("Parallel settlement must advance exactly one state version")
        existing = self.get_scenario_command_batch_by_key(run_id, idempotency_key)
        if existing is not None:
            if (
                existing["batch_kind"] != "parallel"
                or existing["preview_hash"] != preview.settlement_hash
                or existing["preview"] != preview
                or existing.get("authority_basis")
                != authority_basis.model_dump(mode="json")
            ):
                raise ValueError("Idempotency key belongs to a different settlement")
            # Completion cleanup belongs to the original transaction. Repeating
            # it can reject newly submitted actions from a later module run.
            return existing

        # A committed retry refers to the old snapshot by definition. Only new
        # writes must still match the current state version.
        state = self.get_scenario_run_state(run_id)
        if int(state["state_version"]) != preview.starting_run_version:
            raise ValueError("Scenario state changed; recompute the parallel settlement")
        binding = self.get_module_run_contract_binding(run_id)  # type: ignore[attr-defined]
        self._validate_current_authority_basis(
            binding=binding,
            state=state,
            authority_basis=authority_basis,
        )
        commands, resulting = self._settle_with_reactions(
            binding["contract"], state["snapshot"], preview.commands
        )
        if resulting.run_version != preview.starting_run_version + 1:
            raise ValueError("Parallel settlement must advance exactly one state version")

        batch_id = new_id("scenario_batch")
        updated = self.connection.execute(
            """
            UPDATE scenario_run_states
            SET state_version = ?, snapshot_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE run_id = ? AND state_version = ?
            """,
            (
                resulting.run_version,
                json.dumps(
                    resulting.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
                run_id,
                preview.starting_run_version,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Scenario state changed; recompute the parallel settlement")
        self.connection.execute(
            """
            INSERT INTO scenario_command_batches
              (id, run_id, batch_kind, idempotency_key, expected_version,
               result_version, preview_hash, preview_json, authority_basis_json,
               commands_json, snapshot_json)
            VALUES (?, ?, 'parallel', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                run_id,
                idempotency_key,
                preview.starting_run_version,
                resulting.run_version,
                preview.settlement_hash,
                json.dumps(preview.model_dump(mode="json"), ensure_ascii=False),
                authority_basis.model_dump_json(),
                json.dumps(
                    [item.model_dump(mode="json") for item in commands],
                    ensure_ascii=False,
                ),
                json.dumps(
                    resulting.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
            ),
        )
        self._finalize_completed_module_run(run_id, resulting)
        saved = self.get_scenario_command_batch_by_key(run_id, idempotency_key)
        if saved is None:
            raise RuntimeError("Scenario command batch was not persisted")
        return saved

    def commit_action_scenario_batch(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        preview: ResolutionPreview,
        authority_basis: KernelAuthorityBasis,
        outcome: str,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        self._validate_action_authority_identity(
            run_id=run_id,
            preview=preview,
            authority_basis=authority_basis,
        )
        commands = resolved_action_commands(preview, outcome, actor_id=actor_id)
        existing = self.get_scenario_command_batch_by_key(run_id, idempotency_key)
        if existing is not None:
            if (
                existing["batch_kind"] != "action"
                or existing["preview_hash"] != preview.preview_hash
                or existing["preview"] != preview.model_dump(mode="json")
                # The preview includes all possible outcomes. Bind the replay
                # to the selected outcome and actor in its resolution event.
                or existing["commands"][:1] != [commands[0].model_dump(mode="json")]
                or existing.get("authority_basis")
                != authority_basis.model_dump(mode="json")
            ):
                raise ValueError("Idempotency key belongs to a different kernel action")
            return existing
        state = self.get_scenario_run_state(run_id)
        if int(state["state_version"]) != preview.run_version:
            raise ValueError("Scenario state changed; recompute the action preview")
        binding = self.get_module_run_contract_binding(run_id)  # type: ignore[attr-defined]
        self._validate_current_action_authority(
            binding=binding,
            state=state,
            preview=preview,
            authority_basis=authority_basis,
        )
        commands, resulting = self._settle_with_reactions(
            binding["contract"], state["snapshot"], commands
        )
        if resulting.run_version != preview.run_version + 1:
            raise ValueError("Kernel action must advance exactly one state version")
        batch_id = new_id("scenario_batch")
        updated = self.connection.execute(
            """
            UPDATE scenario_run_states
            SET state_version = ?, snapshot_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE run_id = ? AND state_version = ?
            """,
            (
                resulting.run_version,
                json.dumps(resulting.model_dump(mode="json"), ensure_ascii=False),
                run_id,
                preview.run_version,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Scenario state changed; recompute the action preview")
        self.connection.execute(
            """
            INSERT INTO scenario_command_batches
              (id, run_id, batch_kind, idempotency_key, expected_version,
               result_version, preview_hash, preview_json, authority_basis_json,
               commands_json, snapshot_json)
            VALUES (?, ?, 'action', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                run_id,
                idempotency_key,
                preview.run_version,
                resulting.run_version,
                preview.preview_hash,
                json.dumps(preview.model_dump(mode="json"), ensure_ascii=False),
                authority_basis.model_dump_json(),
                json.dumps(
                    [item.model_dump(mode="json") for item in commands],
                    ensure_ascii=False,
                ),
                json.dumps(resulting.model_dump(mode="json"), ensure_ascii=False),
            ),
        )
        self._finalize_completed_module_run(run_id, resulting)
        saved = self.get_scenario_command_batch_by_key(run_id, idempotency_key)
        if saved is None:
            raise RuntimeError("Kernel action command batch was not persisted")
        return saved

    @staticmethod
    def _validate_action_authority_identity(
        *,
        run_id: str,
        preview: ResolutionPreview,
        authority_basis: KernelAuthorityBasis,
    ) -> None:
        if preview.run_id != run_id:
            raise ValueError("Kernel action preview belongs to a different run")
        authority_basis.require_artifact(
            run_id=run_id,
            state_version=preview.run_version,
            preview_hash=preview.preview_hash,
        )

    @staticmethod
    def _validate_current_action_authority(
        *,
        binding: dict[str, Any],
        state: dict[str, Any],
        preview: ResolutionPreview,
        authority_basis: KernelAuthorityBasis,
    ) -> None:
        contract = binding["contract"]
        snapshot = state["snapshot"]
        ScenarioRunStateRepository._validate_current_authority_basis(
            binding=binding,
            state=state,
            authority_basis=authority_basis,
        )
        if (
            preview.contract_id != contract.contract_id
            or preview.scenario_version != contract.source_version
            or snapshot.contract_id != contract.contract_id
            or snapshot.scenario_version != contract.source_version
        ):
            raise ValueError("Scenario authority changed; recompute the action preview")

    @staticmethod
    def _validate_current_authority_basis(
        *,
        binding: dict[str, Any],
        state: dict[str, Any],
        authority_basis: KernelAuthorityBasis,
    ) -> None:
        snapshot = state["snapshot"]
        authority_basis.require_current(
            contract_version_id=str(binding["contract_version_id"]),
            contract_hash=str(binding["contract_hash"]),
            state_contract_version_id=str(state["contract_version_id"]),
            state_version=int(state["state_version"]),
            snapshot_run_id=str(snapshot.run_id),
        )
        if snapshot.run_version != authority_basis.state_version:
            raise ValueError("Scenario authority changed; recompute the kernel preview")

    def _finalize_completed_module_run(
        self,
        run_id: str,
        snapshot: ScenarioSnapshot,
    ) -> None:
        """Make a kernel ending and its operational consequences atomic.

        The scenario snapshot is the authoritative source for whether an ending
        was reached.  Keeping this transition beside the snapshot write prevents
        a completed story from retaining an active module run or runnable jobs.
        """

        if snapshot.status != "completed":
            return
        row = self.connection.execute(
            "SELECT campaign_id FROM campaign_module_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Campaign module run not found: {run_id}")
        campaign_id = str(row["campaign_id"])
        self.connection.execute(
            """
            UPDATE campaign_module_runs
            SET status = 'completed', completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status != 'completed'
            """,
            (run_id,),
        )
        self.connection.execute(
            """
            UPDATE auto_kp_jobs
            SET status = 'cancelled', stage = 'cancelled', next_run_at = NULL,
                locked_by = NULL, locked_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE campaign_id = ? AND run_id = ?
              AND status IN ('queued', 'retry_wait')
            """,
            (campaign_id, run_id),
        )
        self.connection.execute(
            """
            UPDATE player_actions
            SET status = 'rejected', resolved_at = CURRENT_TIMESTAMP
            WHERE campaign_id = ? AND status = 'submitted'
            """,
            (campaign_id,),
        )

    @staticmethod
    def _settle_with_reactions(
        contract: ScenarioContract,
        starting: ScenarioSnapshot,
        commands: tuple[WorldCommand, ...],
    ) -> tuple[tuple[WorldCommand, ...], ScenarioSnapshot]:
        return settle_world_commands(contract, starting, commands)

    @staticmethod
    def _decode_batch(row: Any) -> dict[str, Any]:
        result = dict(row)
        authority_basis_json = result.pop("authority_basis_json", None)
        if authority_basis_json is not None:
            authority_basis_payload = decode_json_field(authority_basis_json, {})
            result["authority_basis"] = KernelAuthorityBasis.model_validate(
                authority_basis_payload
            ).model_dump(mode="json")
        preview_payload = decode_json_field(result.pop("preview_json"), {})
        result["preview"] = (
            ParallelSettlementPreview.model_validate(preview_payload)
            if result["batch_kind"] == "parallel"
            else preview_payload
        )
        result["commands"] = decode_json_field(result.pop("commands_json"), [])
        result["snapshot"] = ScenarioSnapshot.model_validate(
            decode_json_field(result.pop("snapshot_json"), {})
        )
        return result


__all__ = ["ScenarioRunStateRepository"]
