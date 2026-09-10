"""Background worker for evidence-partitioned ScenarioContract compilation."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from threading import Event, RLock, Thread
from typing import Any

from fastapi.encoders import jsonable_encoder

from ai_kp.application.errors import ConflictError
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.infrastructure.llm.model_execution import ModelExecutionSnapshot
from ai_kp.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from ai_kp.infrastructure.scenario_coverage_progress import (
    coverage_attempt_fingerprint,
    restore_coverage_no_progress,
)
from ai_kp.platform.resolution.evidence_compiler import (
    EvidenceBoundCompilationResult,
    EvidenceBoundContractCandidate,
)
from ai_kp.platform.resolution.scenario_authoring import (
    ConstrainedScenarioContractAuthoringAdapter,
    ScenarioAuthoringEvidence,
    ScenarioContractAuthoringResult,
    ScenarioContractReview,
    candidate_after_independent_review,
)
from ai_kp.platform.resolution.scenario_ir_models import ScenarioIrBatch
from ai_kp.platform.resolution.scenario_ir_repair import ScenarioIrRepairPlan
from ai_kp.platform.resolution.source_coverage import SourceCoverageSupplementTarget

_COVERAGE_SUPPLEMENT_TARGET_LIMIT = 64
_FULL_AI_REVIEW_ATTEMPT_LIMIT = 16
_POST_REVIEW_COVERAGE_LIMIT = 12
_AUTHORING_AUTHORITY_REVISION = 46


class _JobCompilationMemo:
    """Reuse only the immediately repeated, byte-identical candidate compilation."""

    def __init__(
        self,
        compile_candidate: Callable[
            [EvidenceBoundContractCandidate], EvidenceBoundCompilationResult
        ],
    ) -> None:
        self._compile_candidate = compile_candidate
        self._key: str | None = None
        self._result: EvidenceBoundCompilationResult | None = None

    def compile(
        self, candidate: EvidenceBoundContractCandidate
    ) -> EvidenceBoundCompilationResult:
        # Candidate JSON includes the contract, evidence, confidence, and
        # assumptions. Contract-only memoization would incorrectly reuse a
        # provenance decision after review changed evidence authority.
        key = candidate.model_dump_json()
        if key != self._key or self._result is None:
            self._result = self._compile_candidate(candidate)
            self._key = key
        return self._result

    def seed(
        self,
        candidate: EvidenceBoundContractCandidate,
        result: EvidenceBoundCompilationResult,
    ) -> None:
        """Restore a compilation authenticated by a persisted candidate fingerprint."""

        self._key = candidate.model_dump_json()
        self._result = result


def _settled_coverage_supplement_summary(
    repo: Repository,
    job_id: str,
    *,
    supplement_cycle: int,
    target_count: int,
    expected_partition_count: int,
) -> dict[str, int]:
    """Build a non-negative summary from exactly one immutable cycle."""

    counts = repo.get_scenario_contract_supplement_cycle_counts(
        job_id,
        supplement_cycle=supplement_cycle,
    )
    if counts["partition_count"] != expected_partition_count:
        raise RuntimeError("Coverage supplement cycle plan changed while processing")
    if counts["pending_partition_count"]:
        raise RuntimeError("Coverage supplement cycle still has unsettled partitions")
    if (
        counts["completed_partition_count"] + counts["failed_partition_count"]
        != counts["partition_count"]
    ):
        raise RuntimeError("Coverage supplement cycle has an unsupported terminal state")
    return {
        "target_count": target_count,
        "partition_count": counts["partition_count"],
        "completed_partition_count": counts["completed_partition_count"],
        "failed_partition_count": counts["failed_partition_count"],
    }


class ScenarioContractWorker:
    """Drain the durable queue and preserve every completed model partition."""

    def __init__(self, settings: Settings, *, poll_interval_seconds: float = 0.5):
        self.settings = settings
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = Event()
        self._wake_event = Event()
        self._ready_event = Event()
        self._thread: Thread | None = None
        self._startup_error: BaseException | None = None
        self._last_error: str | None = None
        self._lock = RLock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._ready_event.clear()
            self._startup_error = None
            self._last_error = None
            self._thread = Thread(
                target=self._run,
                name="ai-kp-scenario-contract-worker",
                daemon=True,
            )
            thread = self._thread
            thread.start()
        if not self._ready_event.wait(timeout=30):
            self.stop()
            raise RuntimeError("Scenario contract worker did not become ready")
        if self._startup_error is not None:
            error = self._startup_error
            self.stop()
            raise RuntimeError("Scenario contract worker failed to start") from error

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
            self._wake_event.set()
        thread.join()
        with self._lock:
            if self._thread is thread:
                self._thread = None

    def wake(self) -> None:
        self._wake_event.set()

    def _run(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = connect(
                self.settings.db_path,
                synchronous=self.settings.sqlite_synchronous,
            )
            init_db(connection)
            repo = Repository(connection)
            repo.recover_interrupted_scenario_contract_jobs()
            repo.resume_unfinished_full_ai_reviews(
                max_review_count=_FULL_AI_REVIEW_ATTEMPT_LIMIT,
                max_coverage_cycle=_POST_REVIEW_COVERAGE_LIMIT,
                authority_revision=_AUTHORING_AUTHORITY_REVISION,
            )
            connection.commit()
            self._ready_event.set()
            while not self._stop_event.is_set():
                try:
                    job = repo.claim_next_scenario_contract_job(
                        worker_id="scenario-contract-worker"
                    )
                    connection.commit()
                except sqlite3.Error as exc:
                    connection.rollback()
                    self._last_error = str(exc)
                    self._wait_for_work()
                    continue
                if job is None:
                    self._wait_for_work()
                    continue
                process_claimed_scenario_contract_job(
                    connection, job, settings=self.settings
                )
        except BaseException as exc:  # noqa: BLE001 - report startup failure
            self._startup_error = exc
            self._last_error = str(exc)
            self._ready_event.set()
        finally:
            if connection is not None:
                connection.close()

    def _wait_for_work(self) -> None:
        self._wake_event.wait(timeout=self.poll_interval_seconds)
        self._wake_event.clear()


def process_claimed_scenario_contract_job(
    connection: sqlite3.Connection,
    job: dict[str, Any],
    *,
    settings: Settings,
) -> None:
    repo = Repository(connection)
    service = ScenarioContractService(repo)
    job_id = str(job["id"])
    expected_attempt = int(job["attempt_count"])
    try:
        model_execution = ModelExecutionSnapshot.capture(repo, settings)
        job = repo.bind_scenario_contract_job_model_generation(
            job_id,
            expected_attempt=expected_attempt,
            model_configuration_version=model_execution.configuration_version,
        )
        connection.commit()
        expected_attempt = int(job["attempt_count"])
        execution_settings = model_execution.settings
        adapter = ScenarioContractService.authoring_adapter(
            OpenAICompatibleClient(
                execution_settings.llm_base_url,
                execution_settings.llm_api_key,
                execution_settings.llm_model,
                max_tokens=8192,
            ),
            str(job["ruleset_id"]),
        )

        def observe_record_repair(
            _plan: ScenarioIrRepairPlan, _model_attempt: int
        ) -> None:
            repo.set_scenario_contract_job_stage(
                job_id,
                expected_attempt=expected_attempt,
                stage="repairing_records",
            )
            connection.commit()

        def compile_with_visible_stage(
            candidate: EvidenceBoundContractCandidate,
        ) -> EvidenceBoundCompilationResult:
            repo.set_scenario_contract_job_stage(
                job_id,
                expected_attempt=expected_attempt,
                stage="analyzing_playability",
            )
            connection.commit()
            return service.evidence_compiler.compile(candidate)

        compilation_memo = _JobCompilationMemo(compile_with_visible_stage)

        while True:
            partition = repo.next_scenario_contract_partition(
                job_id, expected_attempt=expected_attempt
            )
            connection.commit()
            if partition is None:
                break
            repo.set_scenario_contract_job_stage(
                job_id,
                expected_attempt=expected_attempt,
                stage="authoring_partition",
            )
            connection.commit()
            authored = asyncio.run(
                adapter.author_partition(
                    partition["evidence"],
                    ruleset_id=str(job["ruleset_id"]),
                    partition_index=int(partition["partition_index"]),
                    repair_observer=observe_record_repair,
                )
            )
            model_execution.revalidate(repo)
            if authored.batch is None:
                partition_error = " · ".join(authored.validation_errors)[-4000:]
                repo.fail_scenario_contract_partition(
                    job_id,
                    int(partition["partition_index"]),
                    expected_attempt=expected_attempt,
                    error=partition_error,
                    model_attempt_count=authored.attempt_count,
                    validation_errors=authored.validation_errors,
                    repair_diagnostics=authored.repair_diagnostics,
                )
                connection.commit()
                raise RuntimeError(partition_error)
            repo.complete_scenario_contract_partition(
                job_id,
                int(partition["partition_index"]),
                expected_attempt=expected_attempt,
                batch=authored.batch,
                model_attempt_count=authored.attempt_count,
                validation_errors=authored.validation_errors,
                repair_diagnostics=authored.repair_diagnostics,
            )
            connection.commit()

        repo.set_scenario_contract_job_stage(
            job_id, expected_attempt=expected_attempt, stage="assembling"
        )
        # Assembly performs deterministic compilation/playability work. The
        # stage write is durable, but must not keep SQLite's writer lock while
        # that CPU-bound work runs.
        connection.commit()
        evidence, batches = repo.load_scenario_contract_job_inputs(job_id)
        model_attempts, validation_errors, repair_diagnostics = (
            repo.get_scenario_contract_job_authoring_stats(job_id)
        )
        authoring = adapter.assemble(
            evidence,
            batches,
            contract_id=str(job["contract_key"]),
            source_version=int(job["source_version"]),
            ruleset_id=str(job["ruleset_id"]),
            title=str(job["title"]),
            corpus_truncated=bool(job["corpus_truncated"]),
            attempt_count=max(model_attempts, 1),
            validation_errors=validation_errors,
            repair_diagnostics=repair_diagnostics,
        )
        while authoring.candidate is None and len(batches) == 1 and model_attempts < 3:
            repo.set_scenario_contract_job_stage(
                job_id, expected_attempt=expected_attempt, stage="repairing"
            )
            connection.commit()
            repaired = asyncio.run(
                adapter.author_partition(
                    ConstrainedScenarioContractAuthoringAdapter.partitions(evidence)[0],
                    ruleset_id=str(job["ruleset_id"]),
                    partition_index=0,
                    max_attempts=3 - model_attempts,
                    repair_observer=observe_record_repair,
                )
            )
            model_execution.revalidate(repo)
            model_attempts += repaired.attempt_count
            validation_errors = (
                *authoring.validation_errors,
                *repaired.validation_errors,
            )
            repair_diagnostics = (
                *repair_diagnostics,
                *repaired.repair_diagnostics,
            )
            if repaired.batch is None:
                raise RuntimeError(" · ".join(validation_errors)[-4000:])
            batches = (repaired.batch,)
            repo.replace_scenario_contract_partition_result(
                job_id,
                0,
                expected_attempt=expected_attempt,
                batch=repaired.batch,
                model_attempt_count=model_attempts,
                validation_errors=validation_errors,
                repair_diagnostics=repair_diagnostics,
            )
            connection.commit()
            authoring = adapter.assemble(
                evidence,
                batches,
                contract_id=str(job["contract_key"]),
                source_version=int(job["source_version"]),
                ruleset_id=str(job["ruleset_id"]),
                title=str(job["title"]),
                corpus_truncated=bool(job["corpus_truncated"]),
                attempt_count=model_attempts,
                validation_errors=validation_errors,
                repair_diagnostics=repair_diagnostics,
            )
        if authoring.candidate is None:
            authoring_payload = authoring.model_dump(mode="json")
            repo.complete_scenario_contract_job(
                job_id,
                expected_attempt=expected_attempt,
                stage="completed_invalid",
                result=_result_payload(job, authoring_payload=authoring_payload),
            )
            connection.commit()
            return

        recovered_result = job.get("result") or {}
        checkpoint = recovered_result.get("review_checkpoint")
        if checkpoint is None:
            authoring, supplement_summary = _run_coverage_supplements(
                connection,
                repo,
                service,
                adapter,
                job,
                expected_attempt=expected_attempt,
                evidence=evidence,
                batches=batches,
                authoring=authoring,
                repair_observer=observe_record_repair,
                revalidate_model=lambda: model_execution.revalidate(repo),
                compile_candidate=compilation_memo.compile,
            )
        else:
            # A review checkpoint already contains the merged result of cycle 0.
            # Recompiling the pre-checkpoint draft on every restart is both
            # wasteful and semantically stale.
            cycle_zero = repo.get_scenario_contract_supplement_cycle_counts(
                job_id, supplement_cycle=0
            )
            supplement_summary = {
                "target_count": cycle_zero["partition_count"],
                "partition_count": cycle_zero["partition_count"],
                "completed_partition_count": cycle_zero[
                    "completed_partition_count"
                ],
                "failed_partition_count": cycle_zero["failed_partition_count"],
            }
        authoring_payload = authoring.model_dump(mode="json")
        authoring_payload["coverage_supplement"] = supplement_summary
        if authoring.candidate is None:
            repo.complete_scenario_contract_job(
                job_id,
                expected_attempt=expected_attempt,
                stage="completed_invalid",
                result=_result_payload(job, authoring_payload=authoring_payload),
            )
            connection.commit()
            return

        candidate = (
            EvidenceBoundContractCandidate.model_validate(checkpoint)
            if checkpoint is not None
            else authoring.candidate
        )
        candidate = adapter.reconcile_candidate_authority(candidate, evidence)
        review_checkpoint: dict[str, Any] | None = None
        final_review_progress: dict[str, Any] | None = None
        if job["automation_level"] == "ai_kp":
            review_progress = recovered_result.get("review_progress") or {}
            review_history: list[dict[str, Any]] = list(
                review_progress.get("history") or []
            )
            authority_revision = int(
                review_progress.get(
                    "authority_revision", _AUTHORING_AUTHORITY_REVISION
                )
            )
            review_epoch_start = int(
                review_progress.get("review_epoch_start", 0)
            )
            review_epoch_start = min(review_epoch_start, len(review_history))
            completed_coverage_cycle = int(
                review_progress.get("completed_coverage_cycle", 0)
            )
            pending_coverage_cycle = review_progress.get("pending_coverage_cycle")
            if pending_coverage_cycle is not None:
                pending_coverage_cycle = int(pending_coverage_cycle)
                if pending_coverage_cycle <= completed_coverage_cycle:
                    pending_coverage_cycle = None
            coverage_cycle_limit = int(
                review_progress.get(
                    "coverage_cycle_limit", _POST_REVIEW_COVERAGE_LIMIT
                )
            )
            coverage_cycle_limit = max(
                coverage_cycle_limit, _POST_REVIEW_COVERAGE_LIMIT
            )
            coverage_no_progress = review_progress.get("coverage_no_progress")
            if not isinstance(coverage_no_progress, dict):
                coverage_no_progress = None
            post_review_coverage: list[dict[str, int]] = []
            review: Any = None
            remaining_review_attempts = max(
                0,
                _FULL_AI_REVIEW_ATTEMPT_LIMIT
                - (len(review_history) - review_epoch_start),
            )
            for attempt_offset in range(remaining_review_attempts):
                guarded = restore_coverage_no_progress(
                    coverage_no_progress,
                    adapter=adapter,
                    candidate=candidate,
                    evidence=evidence,
                )
                if guarded is not None:
                    guarded_review, guarded_compilation = guarded
                    # This exact bounded task already exhausted a cycle. A later
                    # producer/topology repair changes the authority fingerprint
                    # and naturally re-enables coverage authoring.
                    compilation_memo.seed(candidate, guarded_compilation)
                    review = guarded_review
                    break
                preliminary = compilation_memo.compile(candidate)
                blocking_targets = preliminary.coverage.supplement_targets(
                    blocking_only=True
                )
                coverage_complete = not bool(blocking_targets)
                if (
                    not coverage_complete
                    and completed_coverage_cycle < coverage_cycle_limit
                ):
                    # A model/network failure can interrupt an already-created
                    # cycle. Resume that immutable cycle instead of allocating a
                    # new namespace and duplicating the same coverage targets.
                    supplement_cycle = (
                        pending_coverage_cycle
                        if pending_coverage_cycle is not None
                        else completed_coverage_cycle + 1
                    )
                    repo.save_scenario_contract_job_checkpoint(
                        job_id,
                        expected_attempt=expected_attempt,
                        checkpoint=candidate.model_dump(mode="json"),
                        review_history=review_history,
                        completed_coverage_cycle=completed_coverage_cycle,
                        pending_coverage_cycle=supplement_cycle,
                        authority_revision=authority_revision,
                        review_epoch_start=review_epoch_start,
                        coverage_no_progress=coverage_no_progress,
                    )
                    connection.commit()
                    candidate, coverage_summary = (
                        _run_candidate_coverage_supplements(
                            connection,
                            repo,
                            service,
                            adapter,
                            job,
                            expected_attempt=expected_attempt,
                            evidence=evidence,
                            candidate=candidate,
                            supplement_cycle=supplement_cycle,
                            repair_observer=observe_record_repair,
                            revalidate_model=lambda: model_execution.revalidate(repo),
                            compile_candidate=compilation_memo.compile,
                        )
                    )
                    completed_coverage_cycle = supplement_cycle
                    pending_coverage_cycle = None
                    post_review_coverage.append(coverage_summary)
                    remaining_compilation = compilation_memo.compile(candidate)
                    remaining_targets = (
                        remaining_compilation.coverage.supplement_targets(
                            blocking_only=True
                        )
                    )
                    coverage_complete = not bool(remaining_targets)
                    if remaining_targets == blocking_targets:
                        stalled_review = ScenarioContractReview(
                            decision="reject",
                            review_kind="deterministic_compiler",
                            findings=(
                                (
                                    "Coverage supplementation made no progress for "
                                    "the unchanged blocking targets and authority "
                                    "catalogs."
                                ),
                            ),
                            assumptions_resolved=False,
                        )
                        coverage_no_progress = {
                            "fingerprint": coverage_attempt_fingerprint(
                                adapter, candidate, evidence, remaining_targets
                            ),
                            "blocking_targets": [
                                item.model_dump(mode="json")
                                for item in remaining_targets
                            ],
                            "review": stalled_review.model_dump(mode="json"),
                            "compilation": remaining_compilation.model_dump(
                                mode="json"
                            ),
                        }
                    else:
                        coverage_no_progress = None
                    repo.save_scenario_contract_job_checkpoint(
                        job_id,
                        expected_attempt=expected_attempt,
                        checkpoint=candidate.model_dump(mode="json"),
                        review_history=review_history,
                        completed_coverage_cycle=completed_coverage_cycle,
                        authority_revision=authority_revision,
                        review_epoch_start=review_epoch_start,
                        coverage_no_progress=coverage_no_progress,
                    )
                    connection.commit()
                preliminary = compilation_memo.compile(candidate)
                compiler_review = adapter.review_compilation_failure(
                    candidate, preliminary.report
                )
                while compiler_review is not None:
                    contracted, remaining_review = (
                        adapter.contract_compiler_proven_unreachable_locations(
                            candidate, compiler_review
                        )
                    )
                    if contracted is candidate:
                        break
                    review_history.append(compiler_review.model_dump(mode="json"))
                    candidate = adapter.reconcile_candidate_authority(
                        contracted, evidence
                    )
                    if remaining_review.issues:
                        compiler_review = remaining_review
                        break
                    preliminary = compilation_memo.compile(candidate)
                    compiler_review = adapter.review_compilation_failure(
                        candidate, preliminary.report
                    )
                if compiler_review is not None:
                    review = compiler_review
                    if (
                        restore_coverage_no_progress(
                            coverage_no_progress,
                            adapter=adapter,
                            candidate=candidate,
                            evidence=evidence,
                        )
                        is not None
                    ):
                        coverage_no_progress = {
                            **coverage_no_progress,
                            "review": compiler_review.model_dump(mode="json"),
                        }
                    review_history.append(review.model_dump(mode="json"))
                    repo.save_scenario_contract_job_checkpoint(
                        job_id,
                        expected_attempt=expected_attempt,
                        checkpoint=candidate.model_dump(mode="json"),
                        review_history=review_history,
                        completed_coverage_cycle=completed_coverage_cycle,
                        authority_revision=authority_revision,
                        review_epoch_start=review_epoch_start,
                        coverage_no_progress=coverage_no_progress,
                    )
                    connection.commit()
                    if attempt_offset + 1 >= remaining_review_attempts:
                        break
                    repo.set_scenario_contract_job_stage(
                        job_id,
                        expected_attempt=expected_attempt,
                        stage="repairing_compilation",
                    )
                    connection.commit()
                    repaired_candidate = asyncio.run(
                        adapter.repair_review_rejection(
                            candidate, evidence, compiler_review
                        )
                    )
                    model_execution.revalidate(repo)
                    if repaired_candidate is None:
                        break
                    candidate = adapter.reconcile_candidate_authority(
                        repaired_candidate, evidence
                    )
                    # A topology repair may be followed by a comparatively slow
                    # full compilation and independent review. Persist the exact
                    # source-bound candidate first so restart replay never asks a
                    # weak model to reconstruct the same graph selections.
                    repo.save_scenario_contract_job_checkpoint(
                        job_id,
                        expected_attempt=expected_attempt,
                        checkpoint=candidate.model_dump(mode="json"),
                        review_history=review_history,
                        completed_coverage_cycle=completed_coverage_cycle,
                        authority_revision=authority_revision,
                        review_epoch_start=review_epoch_start,
                        coverage_no_progress=coverage_no_progress,
                    )
                    connection.commit()
                    continue
                repo.set_scenario_contract_job_stage(
                    job_id, expected_attempt=expected_attempt, stage="reviewing"
                )
                connection.commit()
                review = asyncio.run(adapter.review(candidate, evidence))
                model_execution.revalidate(repo)
                review_history.append(review.model_dump(mode="json"))
                repo.save_scenario_contract_job_checkpoint(
                    job_id,
                    expected_attempt=expected_attempt,
                    checkpoint=candidate.model_dump(mode="json"),
                    review_history=review_history,
                    completed_coverage_cycle=completed_coverage_cycle,
                    authority_revision=authority_revision,
                    review_epoch_start=review_epoch_start,
                    coverage_no_progress=coverage_no_progress,
                )
                connection.commit()
                if review.decision == "approve" and coverage_complete:
                    break
                if review.decision == "approve":
                    continue
                if attempt_offset + 1 >= remaining_review_attempts:
                    break
                repo.set_scenario_contract_job_stage(
                    job_id,
                    expected_attempt=expected_attempt,
                    stage="repairing_review",
                )
                connection.commit()
                repaired_candidate = asyncio.run(
                    adapter.repair_review_rejection(candidate, evidence, review)
                )
                model_execution.revalidate(repo)
                if repaired_candidate is None:
                    repaired_candidate = adapter.shrink_unsupported_assumptions(
                        candidate, review
                    )
                if repaired_candidate is None:
                    break
                candidate = adapter.reconcile_candidate_authority(
                    repaired_candidate, evidence
                )
            if review is None:
                raise RuntimeError("Full-AI review budget was exhausted")
            authoring_payload["review"] = review.model_dump(mode="json")
            authoring_payload["review_history"] = review_history
            authoring_payload["post_review_coverage"] = post_review_coverage
            review_checkpoint = candidate.model_dump(mode="json")
            final_review_progress = {
                "history": review_history,
                "completed_coverage_cycle": completed_coverage_cycle,
                "pending_coverage_cycle": None,
                "coverage_cycle_limit": coverage_cycle_limit,
                "authority_revision": authority_revision,
                "review_epoch_start": review_epoch_start,
                "coverage_no_progress": coverage_no_progress,
            }
            stalled = restore_coverage_no_progress(
                coverage_no_progress,
                adapter=adapter,
                candidate=candidate,
                evidence=evidence,
            )
            if stalled is not None:
                # The persisted deterministic result already rejects this exact
                # candidate. Adding review prose to candidate assumptions would
                # invalidate the memo and rerun expensive playability solely for
                # metadata that is already present in the review audit payload.
                _, stalled_compilation = stalled
                compilation_memo.seed(candidate, stalled_compilation)
            else:
                candidate = candidate_after_independent_review(candidate, review)
        else:
            authoring_payload["review"] = {
                "decision": "not_requested",
                "findings": [],
                "assumptions_resolved": False,
            }

        repo.set_scenario_contract_job_stage(
            job_id, expected_attempt=expected_attempt, stage="compiling"
        )
        connection.commit()
        model_execution.revalidate(repo)
        _assert_job_source_unchanged(service, job, evidence)
        compilation = service.authenticate_evidence_bound_compilation(
            str(job["module_id"]),
            candidate,
            compilation_memo.compile(candidate),
        )

        # Re-enter a short serialized section only after deterministic compile.
        # The second fingerprint check closes the read/compile/write TOCTOU gap.
        repo.begin_immediate()
        _assert_job_source_unchanged(service, job, evidence)
        version, auto_published = service.persist_evidence_bound_compilation(
            str(job["module_id"]),
            compilation,
            automation_level=str(job["automation_level"]),
            created_by_member_id=job["created_by_member_id"],
        )
        binding = _bind_published_run(repo, job, version, auto_published)
        if auto_published:
            review_checkpoint = None
        repo.complete_scenario_contract_job(
            job_id,
            expected_attempt=expected_attempt,
            stage=(
                "completed"
                if auto_published
                else "review_rejected"
                if job["automation_level"] == "ai_kp"
                and authoring_payload.get("review", {}).get("decision") == "reject"
                else "completed_draft"
                if version is not None
                else "completed_invalid"
            ),
            result=_result_payload(
                job,
                authoring_payload=authoring_payload,
                compilation=compilation.model_dump(mode="json"),
                version=jsonable_encoder(version),
                auto_published=auto_published,
                binding=jsonable_encoder(binding),
                model=execution_settings.llm_model,
                review_checkpoint=review_checkpoint,
                review_progress=final_review_progress,
            ),
        )
        connection.commit()
    except Exception as exc:  # noqa: BLE001 - persist model/runtime failures
        connection.rollback()
        try:
            repo.fail_scenario_contract_job(
                job_id,
                expected_attempt=expected_attempt,
                error=str(exc),
                retryable=not isinstance(exc, ConflictError),
            )
            connection.commit()
        except Exception:  # noqa: BLE001 - claim may already have been recovered
            connection.rollback()


def _assert_job_source_unchanged(
    service: ScenarioContractService,
    job: dict[str, Any],
    evidence: tuple[ScenarioAuthoringEvidence, ...],
) -> None:
    """Revalidate the exact evidence scope before and after deterministic compile."""

    module_id = str(job["module_id"])
    refreshed_module = service.repo.get_module(module_id)
    refreshed_chunks = service.chunks_cited_by_evidence(
        service.module_chunks(module_id),
        evidence,
    )
    if (
        service.generation_source_fingerprint(refreshed_module, refreshed_chunks)
        != job["source_fingerprint"]
    ):
        raise ConflictError("Module source changed while its contract job was running")


def _run_coverage_supplements(
    connection: sqlite3.Connection,
    repo: Repository,
    service: ScenarioContractService,
    adapter: ConstrainedScenarioContractAuthoringAdapter,
    job: dict[str, Any],
    *,
    expected_attempt: int,
    evidence: tuple[ScenarioAuthoringEvidence, ...],
    batches: tuple[ScenarioIrBatch, ...],
    authoring: ScenarioContractAuthoringResult,
    repair_observer: Callable[[ScenarioIrRepairPlan, int], None],
    revalidate_model: Callable[[], None],
    compile_candidate: Callable[
        [EvidenceBoundContractCandidate], EvidenceBoundCompilationResult
    ],
) -> tuple[ScenarioContractAuthoringResult, dict[str, int]]:
    candidate = authoring.candidate
    if candidate is None or bool(job["corpus_truncated"]):
        return authoring, {
            "target_count": 0,
            "partition_count": 0,
            "completed_partition_count": 0,
            "failed_partition_count": 0,
        }
    preliminary = compile_candidate(candidate)
    targets = preliminary.coverage.supplement_targets()[
        :_COVERAGE_SUPPLEMENT_TARGET_LIMIT
    ]
    groups = service.coverage_supplement_groups(evidence, targets)
    count = repo.ensure_scenario_contract_supplements(
        str(job["id"]),
        expected_attempt=expected_attempt,
        groups=groups,
    )
    connection.commit()
    while True:
        supplement = repo.next_scenario_contract_supplement(
            str(job["id"]), expected_attempt=expected_attempt
        )
        connection.commit()
        if supplement is None:
            break
        repo.set_scenario_contract_job_stage(
            str(job["id"]),
            expected_attempt=expected_attempt,
            stage="supplementing_coverage",
        )
        connection.commit()
        partition_index = len(batches) + int(supplement["supplement_index"])
        if any(
            target.requirement_key == "ending_rule"
            for target in supplement["coverage_targets"]
        ):
            ending_candidates, ending_state_candidates = adapter.ending_catalogs(
                authoring.candidate.contract,
                supplement["evidence"],
                supplement["coverage_targets"],
            )
        else:
            ending_candidates, ending_state_candidates = (), ()
        if any(
            target.requirement_key == "explicit_terminal_method"
            for target in supplement["coverage_targets"]
        ):
            terminal_entity_candidates = adapter.terminal_entity_candidates(
                candidate.contract,
                supplement["evidence"],
                supplement["coverage_targets"],
            )
        else:
            terminal_entity_candidates = ()
        authored = asyncio.run(
            adapter.author_partition(
                supplement["evidence"],
                ruleset_id=str(job["ruleset_id"]),
                partition_index=partition_index,
                coverage_targets=supplement["coverage_targets"],
                record_id_prefix=f"supp{partition_index}_",
                ending_candidates=ending_candidates,
                ending_state_candidates=ending_state_candidates,
                terminal_entity_candidates=terminal_entity_candidates,
                repair_observer=repair_observer,
            )
        )
        revalidate_model()
        if authored.batch is None:
            error = " · ".join(authored.validation_errors)[-4000:]
            repo.fail_scenario_contract_supplement(
                str(job["id"]),
                int(supplement["supplement_index"]),
                expected_attempt=expected_attempt,
                error=error,
                model_attempt_count=authored.attempt_count,
                validation_errors=authored.validation_errors,
                repair_diagnostics=authored.repair_diagnostics,
            )
            connection.commit()
            # A semantic supplement exhausts its own bounded model attempts.
            # Keep processing independent targets so the final coverage report
            # can retain the useful draft and every failed-target diagnostic.
            continue
        repo.complete_scenario_contract_supplement(
            str(job["id"]),
            int(supplement["supplement_index"]),
            expected_attempt=expected_attempt,
            batch=authored.batch,
            model_attempt_count=authored.attempt_count,
            validation_errors=authored.validation_errors,
            repair_diagnostics=authored.repair_diagnostics,
        )
        connection.commit()

    supplements = repo.load_scenario_contract_supplement_batches(
        str(job["id"]), supplement_cycle=0
    )
    summary = _settled_coverage_supplement_summary(
        repo,
        str(job["id"]),
        supplement_cycle=0,
        target_count=len(targets),
        expected_partition_count=count,
    )
    model_attempts, validation_errors, repair_diagnostics = (
        repo.get_scenario_contract_job_authoring_stats(str(job["id"]))
    )
    if not supplements:
        diagnosed_authoring = authoring.model_copy(
            update={
                "attempt_count": max(model_attempts, 1),
                "validation_errors": validation_errors,
                "repair_diagnostics": repair_diagnostics,
            }
        )
        return diagnosed_authoring, summary
    repo.set_scenario_contract_job_stage(
        str(job["id"]), expected_attempt=expected_attempt, stage="assembling_supplements"
    )
    connection.commit()
    supplemented = adapter.assemble(
        evidence,
        batches,
        supplement_batches=supplements,
        contract_id=str(job["contract_key"]),
        source_version=int(job["source_version"]),
        ruleset_id=str(job["ruleset_id"]),
        title=str(job["title"]),
        corpus_truncated=bool(job["corpus_truncated"]),
        attempt_count=max(model_attempts, 1),
        validation_errors=validation_errors,
        repair_diagnostics=repair_diagnostics,
    )
    return supplemented, summary


def _run_candidate_coverage_supplements(
    connection: sqlite3.Connection,
    repo: Repository,
    service: ScenarioContractService,
    adapter: ConstrainedScenarioContractAuthoringAdapter,
    job: dict[str, Any],
    *,
    expected_attempt: int,
    evidence: tuple[ScenarioAuthoringEvidence, ...],
    candidate: EvidenceBoundContractCandidate,
    supplement_cycle: int,
    repair_observer: Callable[[ScenarioIrRepairPlan, int], None],
    revalidate_model: Callable[[], None],
    compile_candidate: Callable[
        [EvidenceBoundContractCandidate], EvidenceBoundCompilationResult
    ],
) -> tuple[EvidenceBoundContractCandidate, dict[str, int]]:
    """Close coverage reopened by review without rebuilding rejected records."""

    targets = compile_candidate(candidate).coverage.supplement_targets(
        blocking_only=True
    )[:_COVERAGE_SUPPLEMENT_TARGET_LIMIT]
    groups = _ending_last_coverage_groups(
        service.coverage_supplement_groups(evidence, targets)
    )
    count = repo.ensure_scenario_contract_supplements(
        str(job["id"]),
        expected_attempt=expected_attempt,
        groups=groups,
        supplement_cycle=supplement_cycle,
    )
    connection.commit()
    recovered_batches = repo.load_scenario_contract_supplement_batches(
        str(job["id"]), supplement_cycle=supplement_cycle
    )
    deferred_endings: list[ScenarioIrBatch] = []

    def merge_or_defer(
        current: EvidenceBoundContractCandidate, batch: ScenarioIrBatch
    ) -> EvidenceBoundContractCandidate:
        proposed_ending_ids = {item.id for item in batch.endings}
        merged = adapter.merge_supplement_batches(current, evidence, (batch,))
        present_ending_ids = {item.ending_id for item in merged.contract.endings}
        if proposed_ending_ids and not proposed_ending_ids <= present_ending_ids:
            deferred_endings.append(batch)
            return current
        return adapter.reconcile_candidate_authority(merged, evidence)

    def retry_deferred(
        current: EvidenceBoundContractCandidate,
    ) -> EvidenceBoundContractCandidate:
        pending = tuple(deferred_endings)
        deferred_endings.clear()
        for pending_batch in pending:
            current = merge_or_defer(current, pending_batch)
        return current

    if recovered_batches:
        # Legacy cycles could persist an ending before a producer from the same
        # logical plan. Merge non-terminal content first, then retain (rather
        # than silently discard) any ending until its referenced producer lands.
        ordered_recovered = tuple(
            batch for batch in recovered_batches if not batch.endings
        ) + tuple(batch for batch in recovered_batches if batch.endings)
        for recovered_batch in ordered_recovered:
            candidate = merge_or_defer(candidate, recovered_batch)
        candidate = retry_deferred(candidate)
    while True:
        supplement = repo.next_scenario_contract_supplement(
            str(job["id"]),
            expected_attempt=expected_attempt,
            supplement_cycle=supplement_cycle,
        )
        connection.commit()
        if supplement is None:
            break
        repo.set_scenario_contract_job_stage(
            str(job["id"]),
            expected_attempt=expected_attempt,
            stage="supplementing_review_coverage",
        )
        connection.commit()
        partition_index = int(supplement["supplement_index"])
        if any(
            target.requirement_key == "ending_rule"
            for target in supplement["coverage_targets"]
        ):
            ending_candidates, ending_state_candidates = adapter.ending_catalogs(
                candidate.contract,
                supplement["evidence"],
                supplement["coverage_targets"],
            )
        else:
            ending_candidates, ending_state_candidates = (), ()
        if any(
            target.requirement_key == "explicit_terminal_method"
            for target in supplement["coverage_targets"]
        ):
            terminal_entity_candidates = adapter.terminal_entity_candidates(
                candidate.contract,
                supplement["evidence"],
                supplement["coverage_targets"],
            )
        else:
            terminal_entity_candidates = ()
        authored = asyncio.run(
            adapter.author_partition(
                supplement["evidence"],
                ruleset_id=str(job["ruleset_id"]),
                partition_index=partition_index,
                coverage_targets=supplement["coverage_targets"],
                record_id_prefix=f"supp{supplement_cycle}_{partition_index}_",
                ending_candidates=ending_candidates,
                ending_state_candidates=ending_state_candidates,
                terminal_entity_candidates=terminal_entity_candidates,
                repair_observer=repair_observer,
            )
        )
        revalidate_model()
        if authored.batch is None:
            repo.fail_scenario_contract_supplement(
                str(job["id"]),
                partition_index,
                expected_attempt=expected_attempt,
                error=" · ".join(authored.validation_errors)[-4000:],
                model_attempt_count=authored.attempt_count,
                validation_errors=authored.validation_errors,
                repair_diagnostics=authored.repair_diagnostics,
            )
        else:
            repo.complete_scenario_contract_supplement(
                str(job["id"]),
                partition_index,
                expected_attempt=expected_attempt,
                batch=authored.batch,
                model_attempt_count=authored.attempt_count,
                validation_errors=authored.validation_errors,
                repair_diagnostics=authored.repair_diagnostics,
            )
        connection.commit()
        if authored.batch is not None:
            # Merge each strict producer before authoring the next supplement.
            # Ending groups are ordered last, so their catalogs are rebuilt from
            # the current post-review contract rather than the cycle-start draft.
            candidate = merge_or_defer(candidate, authored.batch)
            candidate = retry_deferred(candidate)
    summary = _settled_coverage_supplement_summary(
        repo,
        str(job["id"]),
        supplement_cycle=supplement_cycle,
        target_count=len(targets),
        expected_partition_count=count,
    )
    return candidate, {"cycle": supplement_cycle, **summary}


def _ending_last_coverage_groups(
    groups: tuple[
        tuple[
            tuple[ScenarioAuthoringEvidence, ...],
            tuple[SourceCoverageSupplementTarget, ...],
        ],
        ...,
    ],
) -> tuple[
    tuple[
        tuple[ScenarioAuthoringEvidence, ...],
        tuple[SourceCoverageSupplementTarget, ...],
    ],
    ...,
]:
    """Split dependencies so producer records settle before terminal links."""

    producers = []
    terminal_observations = []
    endings = []
    for context, targets in groups:
        ending_targets = tuple(
            item for item in targets if item.requirement_key == "ending_rule"
        )
        observation_targets = tuple(
            item
            for item in targets
            if item.requirement_key == "explicit_terminal_observation"
        )
        producer_targets = tuple(
            item
            for item in targets
            if item.requirement_key
            not in {"ending_rule", "explicit_terminal_observation"}
        )
        if producer_targets:
            producers.append((context, producer_targets))
        if observation_targets:
            # Observation actions are server-materialized by a dedicated,
            # model-free path and must not be mixed into a general prompt.
            terminal_observations.append((context, observation_targets))
        if ending_targets:
            endings.append((context, ending_targets))
    return (*producers, *terminal_observations, *endings)


def _bind_published_run(
    repo: Repository,
    job: dict[str, Any],
    version: dict[str, Any] | None,
    auto_published: bool,
) -> dict[str, Any] | None:
    run_id = job.get("run_id")
    if not auto_published or version is None or not run_id:
        return None
    try:
        return repo.get_module_run_contract_binding(str(run_id))
    except KeyError:
        return ScenarioContractService(repo).bind_run(str(run_id), str(version["id"]))


def _result_payload(
    job: dict[str, Any],
    *,
    authoring_payload: dict[str, Any],
    compilation: dict[str, Any] | None = None,
    version: Any = None,
    auto_published: bool = False,
    binding: Any = None,
    model: str = "",
    review_checkpoint: dict[str, Any] | None = None,
    review_progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "authoring": authoring_payload,
        "compilation": compilation,
        "version": version,
        "auto_published": auto_published,
        "corpus_block_count": int(job["corpus_block_count"]),
        "corpus_total_block_count": int(job["corpus_total_block_count"]),
        "corpus_truncated": bool(job["corpus_truncated"]),
        "model": model,
        "binding": binding,
        "review_checkpoint": review_checkpoint,
        "review_progress": review_progress,
    }


__all__ = ["ScenarioContractWorker", "process_claimed_scenario_contract_job"]
