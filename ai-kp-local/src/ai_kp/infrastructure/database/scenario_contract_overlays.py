"""Persistence for additive, run-scoped executable contract overlays."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.resolution.contracts import ScenarioContract, WorldCommand
from ai_kp.platform.resolution.world_expansion_contract import (
    WorldExpansionContractDecision,
    WorldExpansionContractProposal,
)
from ai_kp.platform.resolution.world_settlement import settle_world_commands


class ScenarioContractOverlayRepository(SQLiteRepository):
    def save_scenario_contract_overlay(
        self,
        *,
        run_id: str,
        proposal: WorldExpansionContractProposal,
        decision: WorldExpansionContractDecision,
        status: str,
        created_by_member_id: str | None,
    ) -> dict[str, Any]:
        existing = self.connection.execute(
            "SELECT * FROM scenario_contract_overlays WHERE run_id = ? AND proposal_key = ?",
            (run_id, proposal.proposal_id),
        ).fetchone()
        if existing is not None:
            decoded = self._decode_overlay(existing)
            if decoded["proposal"] != proposal:
                raise ValueError("Overlay proposal key was reused with different content")
            return decoded
        sequence_no = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS value "
                "FROM scenario_contract_overlays WHERE run_id = ?",
                (run_id,),
            ).fetchone()["value"]
        )
        overlay_id = new_id("scenario_overlay")
        self.connection.execute(
            """
            INSERT INTO scenario_contract_overlays
              (id, run_id, proposal_key, sequence_no, status, base_contract_hash,
               base_state_version, merged_contract_hash, proposal_json, decision_json,
               merged_contract_json, created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                overlay_id,
                run_id,
                proposal.proposal_id,
                sequence_no,
                status,
                proposal.base_contract_hash,
                proposal.base_state_version,
                decision.merged_contract_hash,
                proposal.model_dump_json(),
                decision.model_dump_json(exclude={"merged_contract"}),
                decision.merged_contract.model_dump_json()
                if decision.merged_contract is not None
                else None,
                created_by_member_id,
            ),
        )
        return self.get_scenario_contract_overlay(overlay_id)

    def get_scenario_contract_overlay(self, overlay_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM scenario_contract_overlays WHERE id = ?", (overlay_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Scenario contract overlay not found: {overlay_id}")
        return self._decode_overlay(row)

    def list_scenario_contract_overlays(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM scenario_contract_overlays WHERE run_id = ? ORDER BY sequence_no",
            (run_id,),
        ).fetchall()
        return [self._decode_overlay(row) for row in rows]

    def mark_scenario_contract_overlay_active(
        self, overlay_id: str, *, reviewed_by_member_id: str | None
    ) -> dict[str, Any]:
        updated = self.connection.execute(
            """
            UPDATE scenario_contract_overlays
            SET status = 'active', reviewed_by_member_id = ?,
                activated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'review_required'
            """,
            (reviewed_by_member_id, overlay_id),
        )
        if updated.rowcount != 1:
            current = self.get_scenario_contract_overlay(overlay_id)
            if current["status"] != "active":
                raise ValueError("Only a reviewable overlay can be activated")
        return self.get_scenario_contract_overlay(overlay_id)

    def activate_scenario_contract_overlay(
        self,
        overlay_id: str,
        *,
        reviewed_by_member_id: str | None,
    ) -> dict[str, Any]:
        # The active-run, contract-hash, state-version and activation writes are
        # one serialized authority decision. The savepoint below provides local
        # rollback while the outer transaction owns the SQLite write lock.
        self.begin_immediate()
        overlay = self.get_scenario_contract_overlay(overlay_id)
        if overlay["status"] == "active":
            return overlay
        if overlay["status"] != "review_required" or overlay["merged_contract"] is None:
            raise ValueError("Only a valid reviewable overlay can be activated")
        run_id = str(overlay["run_id"])
        run = self.get_campaign_module_run(run_id)  # type: ignore[attr-defined]
        if run.get("status") != "active":
            raise ValueError("Only an active module run accepts contract overlays")
        state = self.get_scenario_run_state(run_id)  # type: ignore[attr-defined]
        binding = self.get_module_run_contract_binding(run_id)  # type: ignore[attr-defined]
        proposal = overlay["proposal"]
        if binding["contract_hash"] != proposal.base_contract_hash:
            raise ValueError("Scenario contract changed; regenerate the overlay")
        if int(state["state_version"]) != proposal.base_state_version:
            raise ValueError("Scenario state changed; regenerate the overlay")

        merged = overlay["merged_contract"]
        commands = self._activation_commands(
            merged, proposal.records, str(overlay["merged_contract_hash"])
        )
        commands, evolved = settle_world_commands(
            merged,
            state["snapshot"],
            commands,
            cause="world_expansion",
        )
        batch_key = f"world-expansion:{overlay_id}"
        batch_id = new_id("scenario_batch")
        self.connection.execute("SAVEPOINT activate_scenario_overlay")
        try:
            updated = self.connection.execute(
                """
                UPDATE scenario_run_states
                SET state_version = ?, snapshot_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE run_id = ? AND state_version = ?
                """,
                (
                    evolved.run_version,
                    evolved.model_dump_json(),
                    run_id,
                    proposal.base_state_version,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("Scenario state changed; regenerate the overlay")
            self.connection.execute(
                """
                INSERT INTO scenario_command_batches
                  (id, run_id, batch_kind, idempotency_key, expected_version,
                   result_version, preview_hash, preview_json, commands_json, snapshot_json)
                VALUES (?, ?, 'world_expansion', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    run_id,
                    batch_key,
                    proposal.base_state_version,
                    evolved.run_version,
                    overlay["merged_contract_hash"],
                    json.dumps(
                        {
                            "overlay_id": overlay_id,
                            "proposal": proposal.model_dump(mode="json"),
                            "decision": overlay["decision"],
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        [item.model_dump(mode="json") for item in commands],
                        ensure_ascii=False,
                    ),
                    evolved.model_dump_json(),
                ),
            )
            self.mark_scenario_contract_overlay_active(
                overlay_id, reviewed_by_member_id=reviewed_by_member_id
            )
            self._finalize_completed_module_run(run_id, evolved)  # type: ignore[attr-defined]
            self.connection.execute("RELEASE SAVEPOINT activate_scenario_overlay")
        except Exception:
            self.connection.execute("ROLLBACK TO SAVEPOINT activate_scenario_overlay")
            self.connection.execute("RELEASE SAVEPOINT activate_scenario_overlay")
            raise
        return self.get_scenario_contract_overlay(overlay_id)

    @staticmethod
    def _activation_commands(
        merged: ScenarioContract, records: Any, contract_hash: str
    ) -> tuple[WorldCommand, ...]:
        return (
            WorldCommand(
                kind="activate_contract_overlay",
                value=contract_hash,
                payload={"source_version": merged.source_version},
            ),
            *(
                WorldCommand(kind="register_entity", entity_id=item.entity_id, value=item.initial_status)
                for item in records.entities
            ),
            *(
                WorldCommand(kind="register_clock", clock_id=item.clock_id, value=item.initial_value)
                for item in records.clocks
            ),
            *(
                WorldCommand(kind="register_resource", path=item.resource_id, value=item.initial_value)
                for item in records.resources
            ),
        )

    @staticmethod
    def _decode_overlay(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["proposal"] = WorldExpansionContractProposal.model_validate(
            decode_json_field(result.pop("proposal_json"), {})
        )
        result["decision"] = decode_json_field(result.pop("decision_json"), {})
        raw_contract = result.pop("merged_contract_json")
        result["merged_contract"] = (
            ScenarioContract.model_validate(decode_json_field(raw_contract, {}))
            if raw_contract
            else None
        )
        return result


__all__ = ["ScenarioContractOverlayRepository"]
