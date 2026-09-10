"""Versioned persistence and immutable run binding for scenario contracts."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.resolution.action_kernel import ScenarioContract
from ai_kp.platform.resolution.scenario_compiler import (
    ContractValidationReport,
    ScenarioContractCompiler,
)


class ScenarioContractRepository(SQLiteRepository):
    def create_scenario_contract_version(
        self,
        *,
        module_id: str,
        contract: ScenarioContract,
        report: ContractValidationReport,
        created_by_member_id: str | None,
    ) -> dict[str, Any]:
        if not report.valid or not report.contract_hash:
            raise ValueError("Only a valid compiled scenario contract can be persisted")
        self.get_module(module_id)
        existing = self.connection.execute(
            """
            SELECT id FROM scenario_contract_versions
            WHERE module_id = ? AND contract_hash = ?
            """,
            (module_id, report.contract_hash),
        ).fetchone()
        if existing is not None:
            return self.get_scenario_contract_version(str(existing["id"]))
        next_version = int(
            self.connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                FROM scenario_contract_versions WHERE module_id = ?
                """,
                (module_id,),
            ).fetchone()["next_version"]
        )
        version_id = new_id("scenario_contract")
        self.connection.execute(
            """
            INSERT INTO scenario_contract_versions
              (id, module_id, contract_key, version, schema_version, source_version,
               contract_hash, contract_json, validation_json, created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                module_id,
                contract.contract_id,
                next_version,
                contract.schema_version,
                contract.source_version,
                report.contract_hash,
                json.dumps(contract.model_dump(mode="json"), ensure_ascii=False),
                json.dumps(report.model_dump(mode="json"), ensure_ascii=False),
                created_by_member_id,
            ),
        )
        return self.get_scenario_contract_version(version_id)

    def get_scenario_contract_version(self, version_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM scenario_contract_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Scenario contract version not found: {version_id}")
        return self._decode_contract_version(row)

    def list_module_scenario_contract_versions(self, module_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM scenario_contract_versions
            WHERE module_id = ? ORDER BY version DESC
            """,
            (module_id,),
        ).fetchall()
        return [self._decode_contract_version(row) for row in rows]

    def publish_scenario_contract_version(
        self,
        version_id: str,
        *,
        expected_row_version: int,
        published_by_member_id: str | None,
        live_report: ContractValidationReport,
    ) -> dict[str, Any]:
        current = self.get_scenario_contract_version(version_id)
        if current["status"] == "published":
            return current
        if current["status"] != "draft":
            raise ValueError("Only a draft scenario contract can be published")
        if live_report.contract_hash != current["contract_hash"]:
            raise ValueError("Live validation does not match the stored scenario contract")
        live_provenance_ready = live_report.provenance_ready
        stored_validation = current["validation"]
        if (
            not stored_validation.valid
            or not stored_validation.release_ready
            or not stored_validation.provenance_ready
            or not live_report.valid
            or not live_report.release_ready
            or not live_provenance_ready
        ):
            failed = sorted({
                proof.invariant
                for report in (stored_validation, live_report)
                for proof in report.playability.proofs
                if proof.status != "passed"
            })
            compiler_errors = sorted({
                item.code
                for item in live_report.issues
                if item.severity == "error"
            })
            details = [*failed, *compiler_errors]
            if (
                not stored_validation.provenance_ready
                or not live_provenance_ready
            ):
                details.append("source provenance")
            detail = ", ".join(dict.fromkeys(details)) or "current validation"
            raise ValueError(
                "Scenario contract is structurally valid but not release-ready: "
                + detail
            )
        if int(current["row_version"]) != expected_row_version:
            raise ValueError("Scenario contract changed; refresh before publishing")
        self.connection.execute(
            """
            UPDATE scenario_contract_versions
            SET status = 'superseded', row_version = row_version + 1
            WHERE module_id = ? AND status = 'published'
            """,
            (current["module_id"],),
        )
        updated = self.connection.execute(
            """
            UPDATE scenario_contract_versions
            SET status = 'published', row_version = row_version + 1,
                published_by_member_id = ?, published_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'draft' AND row_version = ?
            """,
            (published_by_member_id, version_id, expected_row_version),
        )
        if updated.rowcount != 1:
            raise ValueError("Scenario contract changed; refresh before publishing")
        return self.get_scenario_contract_version(version_id)

    def bind_module_run_contract(
        self,
        *,
        run_id: str,
        contract_version_id: str,
    ) -> dict[str, Any]:
        existing = self.connection.execute(
            "SELECT * FROM module_run_contract_bindings WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if existing is not None:
            if str(existing["contract_version_id"]) != contract_version_id:
                raise ValueError("A module run cannot switch scenario contract versions")
            return self.get_module_run_contract_binding(run_id)
        match = self.connection.execute(
            """
            SELECT 1
            FROM campaign_module_runs r
            JOIN scenario_contract_versions c ON c.module_id = r.module_id
            WHERE r.id = ? AND c.id = ? AND c.status = 'published'
            """,
            (run_id, contract_version_id),
        ).fetchone()
        if match is None:
            raise ValueError("Run and published scenario contract do not belong together")
        self.connection.execute(
            """
            INSERT INTO module_run_contract_bindings (run_id, contract_version_id)
            VALUES (?, ?)
            """,
            (run_id, contract_version_id),
        )
        return self.get_module_run_contract_binding(run_id)

    def migrate_unprogressed_module_run_contract_binding(
        self,
        *,
        run_id: str,
        previous_contract_version_id: str,
        contract_version_id: str,
    ) -> dict[str, Any]:
        """Explicitly rebind a pristine run to one audited replacement version."""

        self.assert_unprogressed_scenario_contract_migration(run_id)
        state = self.connection.execute(
            "SELECT state_version FROM scenario_run_states WHERE run_id = ?",
            (run_id,),
        ).fetchone()

        existing = self.connection.execute(
            "SELECT contract_version_id FROM module_run_contract_bindings WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if existing is None:
            return self.bind_module_run_contract(
                run_id=run_id, contract_version_id=contract_version_id
            )
        if str(existing["contract_version_id"]) != previous_contract_version_id:
            raise ValueError("Scenario contract binding changed during legacy migration")
        target = self.connection.execute(
            """SELECT target.status, target.module_id,
                      previous.module_id AS previous_module_id,
                      run.module_id AS run_module_id
               FROM scenario_contract_versions target
               JOIN scenario_contract_versions previous ON previous.id = ?
               JOIN campaign_module_runs run ON run.id = ?
               WHERE target.id = ?""",
            (previous_contract_version_id, run_id, contract_version_id),
        ).fetchone()
        if (
            target is None
            or str(target["status"]) != "published"
            or len({
                str(target["module_id"]),
                str(target["previous_module_id"]),
                str(target["run_module_id"]),
            })
            != 1
        ):
            raise ValueError(
                "Legacy migration target must be the published contract for the same module"
            )
        updated = self.connection.execute(
            """UPDATE module_run_contract_bindings SET contract_version_id = ?,
                 bound_at = CURRENT_TIMESTAMP
               WHERE run_id = ? AND contract_version_id = ?""",
            (contract_version_id, run_id, previous_contract_version_id),
        )
        if updated.rowcount != 1:
            raise ValueError("Scenario contract binding changed during legacy migration")
        if state is not None:
            self.connection.execute(
                """UPDATE scenario_run_states SET contract_version_id = ?
                   WHERE run_id = ? AND state_version = 0""",
                (contract_version_id, run_id),
            )
        return self.get_module_run_contract_binding(run_id)

    def assert_unprogressed_scenario_contract_migration(self, run_id: str) -> None:
        """Fail closed before creating a replacement for a progressed run."""

        state = self.connection.execute(
            "SELECT state_version FROM scenario_run_states WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        progressed = (
            state is not None and int(state["state_version"]) != 0
        ) or any(
            self.connection.execute(query, (run_id,)).fetchone() is not None
            for query in (
                "SELECT 1 FROM scenario_command_batches WHERE run_id = ? LIMIT 1",
                "SELECT 1 FROM scenario_contract_overlays WHERE run_id = ? LIMIT 1",
                "SELECT 1 FROM kernel_plan_instances WHERE run_id = ? LIMIT 1",
                "SELECT 1 FROM action_resolution_previews WHERE run_id = ? LIMIT 1",
                """SELECT 1 FROM auto_kp_jobs WHERE run_id = ?
                   AND status IN ('queued', 'running', 'retry_wait', 'needs_attention')
                   LIMIT 1""",
            )
        )
        if progressed:
            raise ValueError(
                "Legacy scenario contract requires a new module run because this "
                "run has already progressed; create a fresh run and bind the audited "
                "canonical contract version"
            )

    def get_module_run_contract_binding(self, run_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT b.run_id, b.contract_version_id, b.bound_at,
                   c.contract_hash, c.contract_json, c.version, c.source_version
            FROM module_run_contract_bindings b
            JOIN scenario_contract_versions c ON c.id = b.contract_version_id
            WHERE b.run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Module run has no scenario contract binding: {run_id}")
        result = dict(row)
        base_contract = ScenarioContract.model_validate(
            decode_json_field(result.pop("contract_json"), {})
        )
        result["base_contract_hash"] = result["contract_hash"]
        overlay = self.connection.execute(
            """
            SELECT id, merged_contract_hash, merged_contract_json
            FROM scenario_contract_overlays
            WHERE run_id = ? AND status = 'active'
            ORDER BY sequence_no DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if overlay is None:
            result["contract"] = base_contract
            result["active_overlay_id"] = None
        else:
            result["contract"] = ScenarioContract.model_validate(
                decode_json_field(overlay["merged_contract_json"], {})
            )
            result["contract_hash"] = str(overlay["merged_contract_hash"])
            result["active_overlay_id"] = str(overlay["id"])
        return result

    @staticmethod
    def _decode_contract_version(row: Any) -> dict[str, Any]:
        result = dict(row)
        contract = ScenarioContract.model_validate(
            decode_json_field(result.pop("contract_json"), {})
        )
        validation_payload = decode_json_field(result.pop("validation_json"), {})
        if (
            isinstance(validation_payload, dict)
            and "provenance_ready" not in validation_payload
        ):
            # Reports persisted before the provenance gate remain readable. Derive
            # only the missing field from the immutable contract; do not upgrade an
            # older release decision or discard its other review gates.
            validation_payload["provenance_ready"] = (
                ScenarioContractCompiler.has_complete_provenance(contract)
            )
        result["contract"] = contract
        result["validation"] = ContractValidationReport.model_validate(
            validation_payload
        )
        return result


__all__ = ["ScenarioContractRepository"]
