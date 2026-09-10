"""Model-generation fence for resumable ScenarioContract authoring."""

from __future__ import annotations

import sqlite3


class ScenarioContractModelGenerationFence:
    """Reset only model-derived intermediates when an authoring model changes."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def bind(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        model_configuration_version: int,
    ) -> bool:
        if model_configuration_version < 0:
            raise ValueError("Model configuration version cannot be negative")
        row = self.connection.execute(
            """
            SELECT model_configuration_version
            FROM scenario_contract_jobs
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (job_id, expected_attempt),
        ).fetchone()
        if row is None:
            return False
        if int(row["model_configuration_version"]) == model_configuration_version:
            return True

        self.connection.execute(
            """
            UPDATE scenario_contract_job_partitions
            SET status = 'queued', attempt_count = 0, model_attempt_count = 0,
                validation_errors_json = '[]', repair_diagnostics_json = '[]',
                result_json = NULL, last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ?
            """,
            (job_id,),
        )
        self.connection.execute(
            "DELETE FROM scenario_contract_job_supplements WHERE job_id = ?",
            (job_id,),
        )
        cursor = self.connection.execute(
            """
            UPDATE scenario_contract_jobs
            SET model_configuration_version = ?, attempt_count = 1,
                stage = 'authoring', progress_current = 0,
                progress_total = (
                  SELECT COUNT(*) FROM scenario_contract_job_partitions
                  WHERE job_id = ?
                ),
                result_json = NULL, last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (model_configuration_version, job_id, job_id, expected_attempt),
        )
        return cursor.rowcount == 1


__all__ = ["ScenarioContractModelGenerationFence"]
