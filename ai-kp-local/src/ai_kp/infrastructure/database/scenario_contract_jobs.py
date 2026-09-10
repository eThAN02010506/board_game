"""Durable persistence for resumable ScenarioContract authoring jobs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.scenario_contract_model_generation import (
    ScenarioContractModelGenerationFence,
)
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.resolution.scenario_authoring import ScenarioAuthoringEvidence
from ai_kp.platform.resolution.scenario_ir_models import ScenarioIrBatch
from ai_kp.platform.resolution.scenario_ir_repair import ScenarioIrRepairDiagnostic
from ai_kp.platform.resolution.source_coverage import SourceCoverageSupplementTarget


class ScenarioContractJobClaimLost(ValueError):
    """A recovered or retried job is no longer owned by this worker attempt."""


class ScenarioContractJobRepository(SQLiteRepository):
    def create_scenario_contract_job(
        self,
        *,
        campaign_id: str,
        module_id: str,
        run_id: str | None,
        ruleset_id: str,
        automation_level: str,
        created_by_member_id: str | None,
        source_fingerprint: str,
        contract_key: str,
        title: str,
        evidence_partitions: tuple[tuple[ScenarioAuthoringEvidence, ...], ...],
        corpus_total_block_count: int,
        corpus_truncated: bool,
    ) -> dict[str, Any]:
        if not evidence_partitions:
            raise ValueError("Scenario contract job requires evidence partitions")
        active = self.connection.execute(
            """
            SELECT id FROM scenario_contract_jobs
            WHERE module_id = ? AND status IN ('queued', 'running', 'retry_wait')
            """,
            (module_id,),
        ).fetchone()
        if active is not None:
            return self.get_scenario_contract_job(str(active["id"]))
        job_id = new_id("contractjob")
        block_count = sum(len(partition) for partition in evidence_partitions)
        self.connection.execute(
            """
            INSERT INTO scenario_contract_jobs
              (id, campaign_id, module_id, run_id, ruleset_id, automation_level,
               created_by_member_id, source_fingerprint, contract_key, title,
               corpus_block_count, corpus_total_block_count, corpus_truncated,
               progress_total)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                campaign_id,
                module_id,
                run_id,
                ruleset_id,
                automation_level,
                created_by_member_id,
                source_fingerprint,
                contract_key,
                title,
                block_count,
                corpus_total_block_count,
                int(corpus_truncated),
                len(evidence_partitions),
            ),
        )
        for index, partition in enumerate(evidence_partitions):
            evidence_json = json.dumps(
                [item.model_dump(mode="json") for item in partition],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            self.connection.execute(
                """
                INSERT INTO scenario_contract_job_partitions
                  (job_id, partition_index, evidence_json, evidence_hash)
                VALUES (?, ?, ?, ?)
                """,
                (
                    job_id,
                    index,
                    evidence_json,
                    hashlib.sha256(evidence_json.encode("utf-8")).hexdigest(),
                ),
            )
        return self.get_scenario_contract_job(job_id)

    def get_scenario_contract_job(self, job_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM scenario_contract_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Scenario contract job not found: {job_id}")
        return self._decode_job(row)

    def list_scenario_contract_jobs(
        self, module_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("Invalid ScenarioContract job limit")
        rows = self.connection.execute(
            """
            SELECT * FROM scenario_contract_jobs
            WHERE module_id = ? ORDER BY updated_at DESC, created_at DESC LIMIT ?
            """,
            (module_id, limit),
        ).fetchall()
        return [self._decode_job(row) for row in rows]

    def claim_next_scenario_contract_job(self, *, worker_id: str) -> dict | None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if not self.connection.in_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        row = self.connection.execute(
            """
            SELECT id FROM scenario_contract_jobs
            WHERE status IN ('queued', 'retry_wait')
              AND (next_run_at IS NULL OR next_run_at <= CURRENT_TIMESTAMP)
            -- Give every new job one fair attempt before repeatedly consuming
            -- capacity on older retries. Within the same attempt, favor the most
            -- recent interactive request over a stale recovered backlog.
            ORDER BY attempt_count, created_at DESC, id DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        cursor = self.connection.execute(
            """
            UPDATE scenario_contract_jobs
            SET status = 'running', stage = 'authoring',
                attempt_count = attempt_count + 1, locked_by = ?,
                locked_at = CURRENT_TIMESTAMP, next_run_at = NULL,
                last_error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('queued', 'retry_wait')
            """,
            (worker_id, row["id"]),
        )
        if cursor.rowcount != 1:
            return None
        self.connection.execute(
            """
            UPDATE scenario_contract_job_partitions
            SET status = 'queued', last_error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ? AND status IN ('running', 'failed')
            """,
            (row["id"],),
        )
        self.connection.execute(
            """
            UPDATE scenario_contract_job_supplements
            SET status = 'queued', last_error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ? AND status = 'running'
            """,
            (row["id"],),
        )
        return self.get_scenario_contract_job(str(row["id"]))

    def bind_scenario_contract_job_model_generation(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        model_configuration_version: int,
    ) -> dict[str, Any]:
        """Bind one resumable authoring run to exactly one model generation.

        Partition and supplement outputs are model-authored intermediate state.
        Reusing them after the configured model changes would silently assemble
        one contract from different Agent generations, so a generation change
        restarts only that derived work while preserving the immutable evidence.
        """

        bound = ScenarioContractModelGenerationFence(self.connection).bind(
            job_id,
            expected_attempt=expected_attempt,
            model_configuration_version=model_configuration_version,
        )
        if not bound:
            raise ScenarioContractJobClaimLost("Job claim is no longer current")
        return self.get_scenario_contract_job(job_id)

    def next_scenario_contract_partition(
        self, job_id: str, *, expected_attempt: int
    ) -> dict[str, Any] | None:
        self._assert_claim(job_id, expected_attempt)
        row = self.connection.execute(
            """
            SELECT * FROM scenario_contract_job_partitions
            WHERE job_id = ? AND status = 'queued'
            ORDER BY partition_index LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        self.connection.execute(
            """
            UPDATE scenario_contract_job_partitions
            SET status = 'running', attempt_count = attempt_count + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ? AND partition_index = ? AND status = 'queued'
            """,
            (job_id, row["partition_index"]),
        )
        return self._decode_partition(
            self.connection.execute(
                """
                SELECT * FROM scenario_contract_job_partitions
                WHERE job_id = ? AND partition_index = ?
                """,
                (job_id, row["partition_index"]),
            ).fetchone()
        )

    def complete_scenario_contract_partition(
        self,
        job_id: str,
        partition_index: int,
        *,
        expected_attempt: int,
        batch: ScenarioIrBatch,
        model_attempt_count: int = 1,
        validation_errors: tuple[str, ...] = (),
        repair_diagnostics: tuple[ScenarioIrRepairDiagnostic, ...] = (),
    ) -> None:
        self._assert_claim(job_id, expected_attempt)
        cursor = self.connection.execute(
            """
            UPDATE scenario_contract_job_partitions
            SET status = 'succeeded', result_json = ?, model_attempt_count = ?,
                validation_errors_json = ?, repair_diagnostics_json = ?, last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ? AND partition_index = ? AND status = 'running'
            """,
            (
                batch.model_dump_json(),
                model_attempt_count,
                json.dumps(validation_errors, ensure_ascii=False),
                json.dumps(
                    [item.model_dump(mode="json") for item in repair_diagnostics],
                    ensure_ascii=False,
                ),
                job_id,
                partition_index,
            ),
        )
        if cursor.rowcount != 1:
            raise ScenarioContractJobClaimLost("Partition claim is no longer current")
        completed = self._completed_work_count(job_id)
        self.connection.execute(
            """
            UPDATE scenario_contract_jobs SET progress_current = ?,
              updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (completed, job_id, expected_attempt),
        )

    def ensure_scenario_contract_supplements(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        groups: tuple[
            tuple[
                tuple[ScenarioAuthoringEvidence, ...],
                tuple[SourceCoverageSupplementTarget, ...],
            ],
            ...,
        ],
        supplement_cycle: int = 0,
    ) -> int:
        """Persist one immutable cycle plan, or verify its recovered plan."""

        self._assert_claim(job_id, expected_attempt)
        if supplement_cycle < 0:
            raise ValueError("Coverage supplement cycle must be non-negative")
        rows = self.connection.execute(
            """
            SELECT * FROM scenario_contract_job_supplements
            WHERE job_id = ? AND supplement_cycle = ? ORDER BY supplement_index
            """,
            (job_id, supplement_cycle),
        ).fetchall()
        payloads = [self._supplement_payload(evidence, targets) for evidence, targets in groups]
        if rows:
            existing_hashes = [str(row["payload_hash"]) for row in rows]
            expected_hashes = [item[2] for item in payloads]
            if existing_hashes != expected_hashes:
                # Revision 39 split legacy mixed producer/ending groups so ending
                # catalogs are built after their producers.  The durable plan is
                # still safe to resume when its evidence and logical target set
                # are identical; only the scheduling partition changed.
                existing_logical_targets: list[tuple[str, tuple[str, ...]]] = []
                for row in rows:
                    row_evidence = json.loads(str(row["evidence_json"]))
                    evidence_signature = tuple(
                        sorted(
                            json.dumps(
                                item,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                            for item in row_evidence
                        )
                    )
                    existing_logical_targets.extend(
                        (json.dumps(
                            target,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ), evidence_signature)
                        for target in json.loads(str(row["coverage_targets_json"]))
                    )
                expected_logical_targets: list[tuple[str, tuple[str, ...]]] = []
                for evidence_json, targets_json, _ in payloads:
                    evidence_signature = tuple(
                        sorted(
                            json.dumps(
                                item,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                            for item in json.loads(evidence_json)
                        )
                    )
                    expected_logical_targets.extend(
                        (json.dumps(
                            target,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ), evidence_signature)
                        for target in json.loads(targets_json)
                    )

                if sorted(existing_logical_targets) != sorted(expected_logical_targets):
                    raise ValueError(
                        "Recovered coverage supplement plan no longer matches"
                    )
            return len(rows)
        for index, (evidence_json, targets_json, payload_hash) in enumerate(payloads):
            self.connection.execute(
                """
                INSERT INTO scenario_contract_job_supplements
                  (job_id, supplement_index, evidence_json, coverage_targets_json,
                   payload_hash, supplement_cycle)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    self._next_supplement_index(job_id),
                    evidence_json,
                    targets_json,
                    payload_hash,
                    supplement_cycle,
                ),
            )
        if payloads:
            self.connection.execute(
                """
                UPDATE scenario_contract_jobs
                SET progress_total = progress_total + ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'running' AND attempt_count = ?
                """,
                (len(payloads), job_id, expected_attempt),
            )
        return len(payloads)

    def next_scenario_contract_supplement(
        self, job_id: str, *, expected_attempt: int, supplement_cycle: int = 0
    ) -> dict[str, Any] | None:
        self._assert_claim(job_id, expected_attempt)
        row = self.connection.execute(
            """
            SELECT * FROM scenario_contract_job_supplements
            WHERE job_id = ? AND supplement_cycle = ? AND status = 'queued'
            ORDER BY supplement_index LIMIT 1
            """,
            (job_id, supplement_cycle),
        ).fetchone()
        if row is None:
            return None
        cursor = self.connection.execute(
            """
            UPDATE scenario_contract_job_supplements
            SET status = 'running', attempt_count = attempt_count + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ? AND supplement_index = ? AND status = 'queued'
            """,
            (job_id, row["supplement_index"]),
        )
        if cursor.rowcount != 1:
            raise ScenarioContractJobClaimLost("Supplement claim is no longer current")
        current = self.connection.execute(
            """
            SELECT * FROM scenario_contract_job_supplements
            WHERE job_id = ? AND supplement_index = ?
            """,
            (job_id, row["supplement_index"]),
        ).fetchone()
        decoded = self._decode_supplement(current)
        decoded["evidence"] = self._hydrate_evidence_ancestry(
            job_id, decoded["evidence"]
        )
        return decoded

    def complete_scenario_contract_supplement(
        self,
        job_id: str,
        supplement_index: int,
        *,
        expected_attempt: int,
        batch: ScenarioIrBatch,
        model_attempt_count: int,
        validation_errors: tuple[str, ...] = (),
        repair_diagnostics: tuple[ScenarioIrRepairDiagnostic, ...] = (),
    ) -> None:
        self._assert_claim(job_id, expected_attempt)
        cursor = self.connection.execute(
            """
            UPDATE scenario_contract_job_supplements
            SET status = 'succeeded', result_json = ?, model_attempt_count = ?,
                validation_errors_json = ?, repair_diagnostics_json = ?,
                last_error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ? AND supplement_index = ? AND status = 'running'
            """,
            (
                batch.model_dump_json(),
                model_attempt_count,
                json.dumps(validation_errors, ensure_ascii=False),
                json.dumps(
                    [item.model_dump(mode="json") for item in repair_diagnostics],
                    ensure_ascii=False,
                ),
                job_id,
                supplement_index,
            ),
        )
        if cursor.rowcount != 1:
            raise ScenarioContractJobClaimLost("Supplement claim is no longer current")
        self.connection.execute(
            """
            UPDATE scenario_contract_jobs SET progress_current = ?,
              updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (self._completed_work_count(job_id), job_id, expected_attempt),
        )

    def fail_scenario_contract_supplement(
        self,
        job_id: str,
        supplement_index: int,
        *,
        expected_attempt: int,
        error: str,
        model_attempt_count: int,
        validation_errors: tuple[str, ...] = (),
        repair_diagnostics: tuple[ScenarioIrRepairDiagnostic, ...] = (),
    ) -> None:
        self._assert_claim(job_id, expected_attempt)
        cursor = self.connection.execute(
            """
            UPDATE scenario_contract_job_supplements
            SET status = 'failed', model_attempt_count = ?,
                validation_errors_json = ?, repair_diagnostics_json = ?,
                last_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ? AND supplement_index = ? AND status = 'running'
            """,
            (
                model_attempt_count,
                json.dumps(validation_errors, ensure_ascii=False),
                json.dumps(
                    [item.model_dump(mode="json") for item in repair_diagnostics],
                    ensure_ascii=False,
                ),
                error[:4000],
                job_id,
                supplement_index,
            ),
        )
        if cursor.rowcount != 1:
            raise ScenarioContractJobClaimLost("Supplement claim is no longer current")
        self.connection.execute(
            """
            UPDATE scenario_contract_jobs SET progress_current = ?,
              updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (self._completed_work_count(job_id), job_id, expected_attempt),
        )

    def load_scenario_contract_supplement_batches(
        self, job_id: str, *, supplement_cycle: int | None = None
    ) -> tuple[ScenarioIrBatch, ...]:
        cycle_filter = "" if supplement_cycle is None else " AND supplement_cycle = ?"
        parameters: tuple[Any, ...] = (
            (job_id,) if supplement_cycle is None else (job_id, supplement_cycle)
        )
        rows = self.connection.execute(
            f"""
            SELECT * FROM scenario_contract_job_supplements
            WHERE job_id = ? AND status = 'succeeded'{cycle_filter}
            ORDER BY supplement_index
            """,
            parameters,
        ).fetchall()
        supplements = [self._decode_supplement(row) for row in rows]
        return tuple(item["batch"] for item in supplements)

    def get_scenario_contract_supplement_cycle_counts(
        self,
        job_id: str,
        *,
        supplement_cycle: int,
    ) -> dict[str, int]:
        """Count one immutable supplement cycle without mixing historical cycles."""

        if supplement_cycle < 0:
            raise ValueError("Coverage supplement cycle must be non-negative")
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS partition_count,
                   COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0)
                     AS completed_partition_count,
                   COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0)
                     AS failed_partition_count,
                   COALESCE(SUM(CASE WHEN status IN ('queued', 'running') THEN 1 ELSE 0 END), 0)
                     AS pending_partition_count
            FROM scenario_contract_job_supplements
            WHERE job_id = ? AND supplement_cycle = ?
            """,
            (job_id, supplement_cycle),
        ).fetchone()
        return {
            "partition_count": int(row["partition_count"]),
            "completed_partition_count": int(row["completed_partition_count"]),
            "failed_partition_count": int(row["failed_partition_count"]),
            "pending_partition_count": int(row["pending_partition_count"]),
        }

    def fail_scenario_contract_partition(
        self,
        job_id: str,
        partition_index: int,
        *,
        expected_attempt: int,
        error: str,
        model_attempt_count: int,
        validation_errors: tuple[str, ...] = (),
        repair_diagnostics: tuple[ScenarioIrRepairDiagnostic, ...] = (),
    ) -> None:
        self._assert_claim(job_id, expected_attempt)
        cursor = self.connection.execute(
            """
            UPDATE scenario_contract_job_partitions
            SET status = 'failed', model_attempt_count = ?,
                validation_errors_json = ?, repair_diagnostics_json = ?,
                last_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ? AND partition_index = ? AND status = 'running'
            """,
            (
                model_attempt_count,
                json.dumps(validation_errors, ensure_ascii=False),
                json.dumps(
                    [item.model_dump(mode="json") for item in repair_diagnostics],
                    ensure_ascii=False,
                ),
                error[:4000],
                job_id,
                partition_index,
            ),
        )
        if cursor.rowcount != 1:
            raise ScenarioContractJobClaimLost("Partition claim is no longer current")

    def load_scenario_contract_job_inputs(
        self, job_id: str
    ) -> tuple[tuple[ScenarioAuthoringEvidence, ...], tuple[ScenarioIrBatch, ...]]:
        rows = self.connection.execute(
            """
            SELECT * FROM scenario_contract_job_partitions
            WHERE job_id = ? ORDER BY partition_index
            """,
            (job_id,),
        ).fetchall()
        evidence: list[ScenarioAuthoringEvidence] = []
        batches: list[ScenarioIrBatch] = []
        for row in rows:
            item = self._decode_partition(row)
            evidence.extend(item["evidence"])
            if item["batch"] is None:
                raise ValueError("Scenario contract job has an incomplete partition")
            batches.append(item["batch"])
        return self._hydrate_evidence_ancestry(job_id, tuple(evidence)), tuple(batches)

    def _hydrate_evidence_ancestry(
        self,
        job_id: str,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
    ) -> tuple[ScenarioAuthoringEvidence, ...]:
        """Reapply current server-owned structure to legacy persisted evidence."""

        if not evidence:
            return evidence
        source_ids = tuple(dict.fromkeys(item.source_block_id for item in evidence))
        placeholders = ",".join("?" for _ in source_ids)
        rows = self.connection.execute(
            f"""
            SELECT chunk.id, chunk.heading_level, chunk.section_path_json,
                   chunk.scene_key
            FROM module_chunks AS chunk
            JOIN scenario_contract_jobs AS job ON job.module_id = chunk.module_id
            WHERE job.id = ? AND chunk.id IN ({placeholders})
            """,
            (job_id, *source_ids),
        ).fetchall()
        ancestry = {
            str(row["id"]): (
                row["heading_level"],
                tuple(decode_json_field(row["section_path_json"], [])),
                row["scene_key"],
            )
            for row in rows
        }
        return tuple(
            item.model_copy(
                update={
                    "heading_level": values[0],
                    "section_path": values[1],
                    "scene_key": values[2],
                }
            )
            if (values := ancestry.get(item.source_block_id)) is not None
            else item
            for item in evidence
        )

    def replace_scenario_contract_partition_result(
        self,
        job_id: str,
        partition_index: int,
        *,
        expected_attempt: int,
        batch: ScenarioIrBatch,
        model_attempt_count: int,
        validation_errors: tuple[str, ...],
        repair_diagnostics: tuple[ScenarioIrRepairDiagnostic, ...] = (),
    ) -> None:
        self._assert_claim(job_id, expected_attempt)
        cursor = self.connection.execute(
            """
            UPDATE scenario_contract_job_partitions
            SET result_json = ?, model_attempt_count = ?, validation_errors_json = ?,
                repair_diagnostics_json = ?, last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ? AND partition_index = ? AND status = 'succeeded'
            """,
            (
                batch.model_dump_json(),
                model_attempt_count,
                json.dumps(validation_errors, ensure_ascii=False),
                json.dumps(
                    [item.model_dump(mode="json") for item in repair_diagnostics],
                    ensure_ascii=False,
                ),
                job_id,
                partition_index,
            ),
        )
        if cursor.rowcount != 1:
            raise ScenarioContractJobClaimLost("Partition claim is no longer current")

    def get_scenario_contract_job_authoring_stats(
        self, job_id: str
    ) -> tuple[
        int,
        tuple[str, ...],
        tuple[ScenarioIrRepairDiagnostic, ...],
    ]:
        rows = self.connection.execute(
            """
            SELECT model_attempt_count, validation_errors_json,
                   repair_diagnostics_json
            FROM scenario_contract_job_partitions
            WHERE job_id = ? ORDER BY partition_index
            """,
            (job_id,),
        ).fetchall()
        supplement_rows = self.connection.execute(
            """
            SELECT model_attempt_count, validation_errors_json,
                   repair_diagnostics_json
            FROM scenario_contract_job_supplements
            WHERE job_id = ? ORDER BY supplement_index
            """,
            (job_id,),
        ).fetchall()
        rows = [*rows, *supplement_rows]
        errors = tuple(
            str(error)
            for row in rows
            for error in decode_json_field(row["validation_errors_json"], [])
        )
        repairs = tuple(
            ScenarioIrRepairDiagnostic.model_validate(item)
            for row in rows
            for item in decode_json_field(row["repair_diagnostics_json"], [])
        )
        return sum(int(row["model_attempt_count"]) for row in rows), errors, repairs

    def set_scenario_contract_job_stage(
        self, job_id: str, *, expected_attempt: int, stage: str
    ) -> None:
        cursor = self.connection.execute(
            """
            UPDATE scenario_contract_jobs SET stage = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (stage, job_id, expected_attempt),
        )
        if cursor.rowcount != 1:
            raise ScenarioContractJobClaimLost("Job claim is no longer current")

    def save_scenario_contract_job_checkpoint(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        checkpoint: dict[str, Any],
        review_history: list[dict[str, Any]],
        completed_coverage_cycle: int,
        pending_coverage_cycle: int | None = None,
        authority_revision: int = 0,
        review_epoch_start: int = 0,
        coverage_no_progress: dict[str, Any] | None = None,
    ) -> None:
        """Persist the last whole-contract-valid state during long AI review."""

        progress: dict[str, Any] = {
            "history": review_history,
            "completed_coverage_cycle": completed_coverage_cycle,
            "pending_coverage_cycle": pending_coverage_cycle,
            "authority_revision": authority_revision,
            "review_epoch_start": review_epoch_start,
        }
        if coverage_no_progress is not None:
            progress["coverage_no_progress"] = coverage_no_progress
        payload = {
            "review_checkpoint": checkpoint,
            "review_progress": progress,
        }
        cursor = self.connection.execute(
            """
            UPDATE scenario_contract_jobs SET result_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                job_id,
                expected_attempt,
            ),
        )
        if cursor.rowcount != 1:
            raise ScenarioContractJobClaimLost("Job claim is no longer current")

    def complete_scenario_contract_job(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        result: dict[str, Any],
        stage: str = "completed",
    ) -> dict[str, Any]:
        cursor = self.connection.execute(
            """
            UPDATE scenario_contract_jobs
            SET status = 'succeeded', stage = ?, result_json = ?,
                progress_current = progress_total, locked_by = NULL, locked_at = NULL,
                last_error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (
                stage,
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                job_id,
                expected_attempt,
            ),
        )
        if cursor.rowcount != 1:
            raise ScenarioContractJobClaimLost("Job claim is no longer current")
        return self.get_scenario_contract_job(job_id)

    def fail_scenario_contract_job(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        error: str,
        retryable: bool = True,
    ) -> dict[str, Any]:
        job = self.get_scenario_contract_job(job_id)
        retry = retryable and int(job["attempt_count"]) < int(job["max_attempts"])
        status = "retry_wait" if retry else "failed"
        stage = "waiting_retry" if retry else "failed"
        modifier = f"+{min(60, 2 ** int(job['attempt_count']))} seconds"
        cursor = self.connection.execute(
            """
            UPDATE scenario_contract_jobs
            SET status = ?, stage = ?, last_error = ?, locked_by = NULL,
                locked_at = NULL, retryable = ?,
                next_run_at = CASE WHEN ? THEN datetime(CURRENT_TIMESTAMP, ?) ELSE NULL END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (
                status,
                stage,
                error[:4000],
                int(retryable),
                int(retry),
                modifier,
                job_id,
                expected_attempt,
            ),
        )
        if cursor.rowcount != 1:
            raise ScenarioContractJobClaimLost("Job claim is no longer current")
        if not retry:
            self.connection.execute(
                """
                UPDATE scenario_contract_job_partitions
                SET status = 'failed', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND status = 'running'
                """,
                (error[:4000], job_id),
            )
            self.connection.execute(
                """
                UPDATE scenario_contract_job_supplements
                SET status = 'failed', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND status = 'running'
                """,
                (error[:4000], job_id),
            )
            self.connection.execute(
                """
                UPDATE scenario_contract_jobs SET progress_current = ?
                WHERE id = ? AND status = 'failed'
                """,
                (self._completed_work_count(job_id), job_id),
            )
        return self.get_scenario_contract_job(job_id)

    def retry_scenario_contract_job(self, job_id: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            """
            UPDATE scenario_contract_jobs
            SET status = 'queued', stage = 'queued', attempt_count = 0,
                next_run_at = NULL, locked_by = NULL, locked_at = NULL,
                last_error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'failed' AND retryable = 1
            """,
            (job_id,),
        )
        if cursor.rowcount != 1:
            current = self.get_scenario_contract_job(job_id)
            if current["status"] == "failed" and not current["retryable"]:
                raise ValueError(
                    "Scenario contract job cannot be retried because its source snapshot is stale"
                )
            raise ValueError(
                f"Only failed scenario contract jobs can be retried; current status is {current['status']}"
            )
        self.connection.execute(
            """
            UPDATE scenario_contract_job_partitions SET status = 'queued',
                last_error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ? AND status != 'succeeded'
            """,
            (job_id,),
        )
        self.connection.execute(
            """
            UPDATE scenario_contract_job_supplements SET status = 'queued',
                last_error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ? AND status != 'succeeded'
            """,
            (job_id,),
        )
        self.connection.execute(
            """
            UPDATE scenario_contract_jobs SET progress_current = ?
            WHERE id = ? AND status = 'queued'
            """,
            (self._completed_work_count(job_id), job_id),
        )
        return self.get_scenario_contract_job(job_id)

    def resume_rejected_scenario_contract_review(
        self, module_id: str, source_fingerprint: str
    ) -> dict[str, Any] | None:
        """Create one auditable continuation of the latest rejected review.

        A completed job is immutable history.  Reusing its id for an explicit
        semantic retry makes clients observe the same resource changing from a
        terminal state back to an active state, which is both hard to poll
        correctly and erases how many user-requested review passes occurred.
        The continuation therefore gets a fresh id while reusing only the
        source snapshot and durable model intermediates.  The model-generation
        fence will discard those intermediates if the configured model changed.
        """

        self.begin_immediate()
        active = self.connection.execute(
            """
            SELECT id FROM scenario_contract_jobs
            WHERE module_id = ? AND status IN ('queued', 'running', 'retry_wait')
            """,
            (module_id,),
        ).fetchone()
        if active is not None:
            return self.get_scenario_contract_job(str(active["id"]))

        row = self.connection.execute(
            """
            SELECT * FROM scenario_contract_jobs
            WHERE module_id = ? AND source_fingerprint = ? AND status = 'succeeded'
              AND automation_level = 'ai_kp'
              AND COALESCE(json_extract(result_json, '$.auto_published'), 0) = 0
              AND json_type(result_json, '$.review_checkpoint') = 'object'
            ORDER BY updated_at DESC, created_at DESC LIMIT 1
            """,
            (module_id, source_fingerprint),
        ).fetchone()
        if row is None:
            return None
        previous_job_id = str(row["id"])
        current = self.get_scenario_contract_job(previous_job_id)
        completed_result = current.get("result") or {}
        checkpoint = completed_result.get("review_checkpoint")
        review_progress = completed_result.get("review_progress")
        if review_progress is None:
            latest_cycle = self.connection.execute(
                """
                SELECT COALESCE(MAX(supplement_cycle), 0) AS cycle
                FROM scenario_contract_job_supplements WHERE job_id = ?
                """,
                (row["id"],),
            ).fetchone()["cycle"]
            review_progress = {
                "history": (
                    completed_result.get("authoring", {}).get("review_history", [])
                ),
                "completed_coverage_cycle": int(latest_cycle),
                "pending_coverage_cycle": None,
            }
        previous_cycle = int(review_progress.get("completed_coverage_cycle", 0))
        history = list(review_progress.get("history") or [])
        review_progress = {
            **review_progress,
            "history": history,
            "coverage_cycle_limit": previous_cycle + 3,
            # This endpoint represents an explicit new semantic review pass,
            # unlike retrying a transient worker failure. Preserve the audit
            # trail but grant the new pass its own bounded review epoch.
            "review_epoch_start": len(history),
        }
        continuation_id = new_id("contractjob")
        result_json = (
            json.dumps(
                {
                    "review_checkpoint": checkpoint,
                    "review_progress": review_progress,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if checkpoint is not None
            else None
        )
        self.connection.execute(
            """
            INSERT INTO scenario_contract_jobs
              (id, campaign_id, module_id, run_id, ruleset_id, automation_level,
               created_by_member_id, source_fingerprint, contract_key,
               source_version, title, corpus_block_count,
               corpus_total_block_count, corpus_truncated, status, stage,
               progress_current, progress_total, attempt_count, max_attempts,
               retryable, result_json, model_configuration_version)
            SELECT ?, campaign_id, module_id, run_id, ruleset_id,
                   automation_level, created_by_member_id, source_fingerprint,
                   contract_key, source_version, title, corpus_block_count,
                   corpus_total_block_count, corpus_truncated, 'queued',
                   'queued', 0, progress_total, 0, max_attempts, 1, ?,
                   model_configuration_version
            FROM scenario_contract_jobs WHERE id = ? AND status = 'succeeded'
            """,
            (continuation_id, result_json, previous_job_id),
        )
        self.connection.execute(
            """
            INSERT INTO scenario_contract_job_partitions
              (job_id, partition_index, evidence_json, evidence_hash, status,
               attempt_count, model_attempt_count, validation_errors_json,
               result_json, last_error, repair_diagnostics_json)
            SELECT ?, partition_index, evidence_json, evidence_hash, status,
                   attempt_count, model_attempt_count, validation_errors_json,
                   result_json, last_error, repair_diagnostics_json
            FROM scenario_contract_job_partitions WHERE job_id = ?
            """,
            (continuation_id, previous_job_id),
        )
        self.connection.execute(
            """
            INSERT INTO scenario_contract_job_supplements
              (job_id, supplement_index, evidence_json, payload_hash,
               coverage_targets_json, status, attempt_count,
               model_attempt_count, validation_errors_json,
               repair_diagnostics_json, result_json, last_error,
               supplement_cycle)
            SELECT ?, supplement_index, evidence_json, payload_hash,
                   coverage_targets_json, status, attempt_count,
                   model_attempt_count, validation_errors_json,
                   repair_diagnostics_json, result_json, last_error,
                   supplement_cycle
            FROM scenario_contract_job_supplements WHERE job_id = ?
            """,
            (continuation_id, previous_job_id),
        )
        self.connection.execute(
            """
            UPDATE scenario_contract_jobs SET progress_current = ?
            WHERE id = ? AND status = 'queued'
            """,
            (self._completed_work_count(continuation_id), continuation_id),
        )
        return self.get_scenario_contract_job(continuation_id)

    def resume_unfinished_full_ai_reviews(
        self,
        *,
        max_review_count: int,
        max_coverage_cycle: int,
        authority_revision: int = 0,
    ) -> int:
        """Resume bounded full-AI drafts after startup without a KP click."""

        if max_review_count < 1 or max_coverage_cycle < 0 or authority_revision < 0:
            raise ValueError("Invalid full-AI continuation budget")
        rows = self.connection.execute(
            """
            SELECT id, module_id FROM scenario_contract_jobs AS candidate
            WHERE status IN ('succeeded', 'failed') AND automation_level = 'ai_kp'
              AND COALESCE(json_extract(result_json, '$.auto_published'), 0) = 0
              AND (
                  json_type(result_json, '$.review_checkpoint') = 'object'
                  OR COALESCE(
                      json_extract(result_json, '$.review_progress.rebuild_required'),
                      0
                  ) = 1
              )
              AND NOT EXISTS (
                  SELECT 1 FROM scenario_contract_jobs AS active
                  WHERE active.module_id = candidate.module_id
                    AND active.status IN ('queued', 'running')
              )
            ORDER BY updated_at DESC, created_at DESC, id DESC
            """
        ).fetchall()
        resumed = 0
        resumed_modules: set[str] = set()
        for row in rows:
            module_id = str(row["module_id"])
            if module_id in resumed_modules:
                continue
            current = self.get_scenario_contract_job(str(row["id"]))
            completed = current.get("result") or {}
            progress = completed.get("review_progress") or {}
            coverage_no_progress = progress.get("coverage_no_progress")
            history = progress.get("history")
            if history is None:
                history = completed.get("authoring", {}).get("review_history", [])
            stored_revision = int(progress.get("authority_revision", 0))
            authority_changed = stored_revision < authority_revision
            if current["status"] == "failed" and stored_revision >= authority_revision:
                continue
            review_epoch_start = int(progress.get("review_epoch_start", 0))
            if authority_changed:
                stored_revision = authority_revision
                review_epoch_start = len(history)
            review_epoch_start = min(review_epoch_start, len(history))
            if len(history) - review_epoch_start >= max_review_count:
                continue
            latest_cycle = int(
                self.connection.execute(
                    """
                    SELECT COALESCE(MAX(supplement_cycle), 0) AS cycle
                    FROM scenario_contract_job_supplements WHERE job_id = ?
                    """,
                    (row["id"],),
                ).fetchone()["cycle"]
            )
            progress = {
                "history": history,
                "completed_coverage_cycle": max(
                    int(progress.get("completed_coverage_cycle", 0)), latest_cycle
                ),
                "pending_coverage_cycle": None,
                # A new authority revision may invalidate prior review repairs and
                # reveal hard coverage gaps. Give that migration a fresh bounded
                # supplement window while retaining the absolute cycle cursor.
                "coverage_cycle_limit": (
                    max(max_coverage_cycle, latest_cycle + 3)
                    if authority_changed
                    else max_coverage_cycle
                ),
                "authority_revision": stored_revision,
                "review_epoch_start": review_epoch_start,
            }
            if not authority_changed and isinstance(coverage_no_progress, dict):
                progress["coverage_no_progress"] = coverage_no_progress
            if authority_changed:
                progress["rebuild_required"] = True
            cursor = self.connection.execute(
                """
                UPDATE scenario_contract_jobs
                SET status = 'queued', stage = 'queued', attempt_count = 0,
                    next_run_at = NULL, locked_by = NULL, locked_at = NULL,
                    last_error = NULL, result_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status IN ('succeeded', 'failed')
                """,
                (
                    json.dumps(
                        {
                            # Derived review repairs are only authoritative under
                            # the revision that produced them. Rebuild from the
                            # persisted base/cycle-zero batches after an authority
                            # migration; those settled batches are replayed without
                            # another model call.
                            "review_checkpoint": (
                                None
                                if authority_changed
                                else completed["review_checkpoint"]
                            ),
                            "review_progress": progress,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    row["id"],
                ),
            )
            resumed += int(cursor.rowcount)
            if cursor.rowcount:
                resumed_modules.add(module_id)
        return resumed

    def recover_interrupted_scenario_contract_jobs(self) -> int:
        rows = self.connection.execute(
            "SELECT id, attempt_count, max_attempts FROM scenario_contract_jobs WHERE status = 'running'"
        ).fetchall()
        recovered = 0
        for row in rows:
            retry = int(row["attempt_count"]) < int(row["max_attempts"])
            self.connection.execute(
                """
                UPDATE scenario_contract_job_partitions SET status = 'queued',
                    last_error = 'Worker interrupted', updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND status = 'running'
                """,
                (row["id"],),
            )
            self.connection.execute(
                """
                UPDATE scenario_contract_job_supplements SET status = 'queued',
                    last_error = 'Worker interrupted', updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND status = 'running'
                """,
                (row["id"],),
            )
            cursor = self.connection.execute(
                """
                UPDATE scenario_contract_jobs SET status = ?, stage = ?,
                    locked_by = NULL, locked_at = NULL, next_run_at = NULL,
                    last_error = '服务中断；已从最后完成的分区恢复',
                    updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'
                """,
                (
                    "queued" if retry else "failed",
                    "queued" if retry else "failed",
                    row["id"],
                ),
            )
            recovered += int(cursor.rowcount)
        return recovered

    def _assert_claim(self, job_id: str, expected_attempt: int) -> None:
        row = self.connection.execute(
            """
            SELECT 1 FROM scenario_contract_jobs
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (job_id, expected_attempt),
        ).fetchone()
        if row is None:
            raise ScenarioContractJobClaimLost("Job claim is no longer current")

    def _completed_work_count(self, job_id: str) -> int:
        return int(
            self.connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM scenario_contract_job_partitions
                   WHERE job_id = ? AND status = 'succeeded') +
                  (SELECT COUNT(*) FROM scenario_contract_job_supplements
                   WHERE job_id = ? AND status IN ('succeeded', 'failed')) AS count
                """,
                (job_id, job_id),
            ).fetchone()["count"]
        )

    def _next_supplement_index(self, job_id: str) -> int:
        row = self.connection.execute(
            """
            SELECT COALESCE(MAX(supplement_index), -1) + 1 AS next_index
            FROM scenario_contract_job_supplements WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        return int(row["next_index"])

    @staticmethod
    def _supplement_payload(
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        targets: tuple[SourceCoverageSupplementTarget, ...],
    ) -> tuple[str, str, str]:
        evidence_json = json.dumps(
            [item.model_dump(mode="json") for item in evidence],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        targets_json = json.dumps(
            [item.model_dump(mode="json") for item in targets],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_hash = hashlib.sha256(
            f"{evidence_json}\0{targets_json}".encode()
        ).hexdigest()
        return evidence_json, targets_json, payload_hash

    @staticmethod
    def _decode_job(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        result["corpus_truncated"] = bool(result["corpus_truncated"])
        result["retryable"] = bool(result["retryable"])
        raw_result = result.pop("result_json")
        result["result"] = (
            decode_json_field(raw_result, None) if raw_result is not None else None
        )
        return result

    @staticmethod
    def _decode_partition(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        evidence_json = result.pop("evidence_json")
        if hashlib.sha256(evidence_json.encode("utf-8")).hexdigest() != result["evidence_hash"]:
            raise ValueError("Scenario contract partition evidence hash mismatch")
        raw_evidence = decode_json_field(evidence_json, [])
        result["evidence"] = tuple(
            ScenarioAuthoringEvidence.model_validate(item) for item in raw_evidence
        )
        result_json = result.pop("result_json")
        raw_batch = (
            decode_json_field(result_json, None) if result_json is not None else None
        )
        result["batch"] = (
            ScenarioIrBatch.model_validate(raw_batch) if raw_batch is not None else None
        )
        result["validation_errors"] = tuple(
            decode_json_field(result.pop("validation_errors_json"), [])
        )
        result["repair_diagnostics"] = tuple(
            ScenarioIrRepairDiagnostic.model_validate(item)
            for item in decode_json_field(
                result.pop("repair_diagnostics_json"), []
            )
        )
        return result

    @classmethod
    def _decode_supplement(cls, row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        evidence_json = result.pop("evidence_json")
        targets_json = result.pop("coverage_targets_json")
        payload_hash = hashlib.sha256(
            f"{evidence_json}\0{targets_json}".encode()
        ).hexdigest()
        if payload_hash != result["payload_hash"]:
            raise ValueError("Scenario contract supplement payload hash mismatch")
        result["evidence"] = tuple(
            ScenarioAuthoringEvidence.model_validate(item)
            for item in decode_json_field(evidence_json, [])
        )
        result["coverage_targets"] = tuple(
            SourceCoverageSupplementTarget.model_validate(item)
            for item in decode_json_field(targets_json, [])
        )
        result_json = result.pop("result_json")
        raw_batch = (
            decode_json_field(result_json, None) if result_json is not None else None
        )
        result["batch"] = (
            ScenarioIrBatch.model_validate(raw_batch) if raw_batch is not None else None
        )
        result["validation_errors"] = tuple(
            decode_json_field(result.pop("validation_errors_json"), [])
        )
        result["repair_diagnostics"] = tuple(
            ScenarioIrRepairDiagnostic.model_validate(item)
            for item in decode_json_field(
                result.pop("repair_diagnostics_json"), []
            )
        )
        return result


__all__ = [
    "ScenarioContractJobClaimLost",
    "ScenarioContractJobRepository",
]
