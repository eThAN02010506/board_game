from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from ai_kp.application.ports.repositories import RulebookStore
from ai_kp.platform.knowledge.ports import (
    OriginalTextIndex,
    OriginalTextIndexFactory,
    OriginalTextIndexUnavailableError,
    RulebookExtractor,
)
from ai_kp.platform.ports.llm import LlmClient
from ai_kp.rule_authoring.agent import PROMPT_VERSION, RuleExtractionAgent
from ai_kp.rule_authoring.engine import execute_rule
from ai_kp.rule_authoring.models import (
    RuleObject,
    RuleReviewSubmission,
    RuleStatus,
)
from ai_kp.rule_authoring.review import (
    approval_allows_execution,
    evaluate_golden_cases,
)
from ai_kp.rule_authoring.validation import RuleValidator


class RulebookService:
    def __init__(
        self,
        repo: RulebookStore,
        *,
        extractor: RulebookExtractor,
        index_factory: OriginalTextIndexFactory,
    ):
        self.repo = repo
        self.extractor = extractor
        self.index_factory = index_factory

    def ingest_pdf(
        self,
        data: bytes,
        filename: str,
        *,
        ruleset_id: str,
        title: str | None = None,
    ) -> dict:
        extracted = self.extractor(data, filename)
        source = self.repo.create_rule_source(
            ruleset_id=ruleset_id,
            title=title or extracted.title,
            source_filename=filename,
            source_hash=extracted.source_hash,
            page_count=extracted.page_count,
            metadata=extracted.metadata,
        )
        if source["chunk_count"] == 0:
            self.repo.replace_rule_chunks(source["id"], extracted.chunks)
            self.repo.set_rule_source_status(source["id"], "extracted")
        return self.repo.get_rule_source(source["id"])

    def _index(self, source: dict) -> OriginalTextIndex:
        return self.index_factory(source["ruleset_id"], source["id"])

    async def index_source(self, source_id: str) -> dict:
        source = self.repo.get_rule_source(source_id)
        self.repo.begin_immediate()
        try:
            chunks = self.repo.list_rule_chunks(source_id)
            index = self._index(source)
            run = self.repo.create_ingestion_run(source_id, stage="minirag_index")
            self.repo.set_rule_source_status(source_id, "indexing")
            # SQLite permits one writer. Publish the durable claim before waiting on
            # the external index so live table writes are never blocked by this job.
            self.repo.commit()
        except Exception:
            self.repo.rollback()
            raise
        try:
            mapping = await index.index_chunks(chunks)
            self.repo.begin_immediate()
            completed = self.repo.transition_ingestion_run(
                run["id"],
                expected_status="running",
                status="completed",
                processed_count=len(mapping),
                cursor_page=source["page_count"],
            )
            if completed is None:
                self.repo.rollback()
                return self.repo.get_rule_source(source_id)
            for chunk_id, mini_id in mapping.items():
                self.repo.mark_rule_chunk(chunk_id, minirag_doc_id=mini_id)
            self.repo.set_rule_source_status(source_id, "ready")
            self.repo.commit()
        except Exception as exc:
            self.repo.rollback()
            self.repo.begin_immediate()
            failed = self.repo.transition_ingestion_run(
                run["id"],
                expected_status="running",
                status="failed",
                error_text=str(exc)[:2000],
            )
            if failed is not None:
                self.repo.set_rule_source_status(source_id, "failed")
                self.repo.commit()
            else:
                self.repo.rollback()
            raise
        return self.repo.get_rule_source(source_id)

    async def extract_rules(
        self,
        source_id: str,
        llm: LlmClient,
        *,
        model_name: str,
        limit: int = 10,
        retry_failed: bool = False,
    ) -> dict:
        source = self.repo.get_rule_source(source_id)
        self.repo.begin_immediate()
        try:
            if retry_failed:
                self.repo.reset_failed_rule_chunks(source_id)
            chunks = self.repo.list_rule_chunks(
                source_id, extraction_status="pending", limit=limit
            )
            run = self.repo.create_ingestion_run(
                source_id,
                stage="rule_extraction",
                agent_model=model_name,
                prompt_version=PROMPT_VERSION,
            )
            self.repo.commit()
        except Exception:
            self.repo.rollback()
            raise
        agent = RuleExtractionAgent(llm)
        validator = RuleValidator(self.repo)
        processed = accepted = rejected = 0
        try:
            for chunk in chunks:
                attempt = self.repo.claim_rule_chunk_for_extraction(
                    chunk["id"],
                    run_id=run["id"],
                )
                # The processing state is a durable claim, not a write lock held
                # for the duration of a potentially slow model request.
                try:
                    self.repo.commit()
                except Exception:
                    if attempt is not None:
                        self._best_effort_fail_extraction_claim(
                            chunk["id"],
                            attempt,
                        )
                    raise
                if attempt is None:
                    continue
                try:
                    candidates = await agent.extract(chunk, source["ruleset_id"])
                # Model and parser failures are persisted per chunk so later chunks
                # can continue and the failed chunk can be retried independently.
                except Exception as exc:  # noqa: BLE001
                    try:
                        self.repo.begin_immediate()
                        run_is_current = self.repo.ingestion_run_is_current(
                            run["id"],
                            expected_status="running",
                        )
                        claim_is_current = (
                            self.repo.rule_chunk_extraction_claim_is_current(
                                chunk["id"],
                                expected_attempt=attempt,
                            )
                        )
                        if not run_is_current or not claim_is_current:
                            self.repo.rollback()
                            if not run_is_current:
                                self._best_effort_fail_extraction_claim(
                                    chunk["id"],
                                    attempt,
                                )
                            continue
                        self.repo.record_rule_validation_issue(
                            source_id,
                            validation_layer="agent_output",
                            error_text=str(exc),
                            chunk_id=chunk["id"],
                            run_id=run["id"],
                        )
                        if not self.repo.mark_rule_chunk(
                            chunk["id"],
                            extraction_status="failed",
                            expected_attempt=attempt,
                        ):
                            self.repo.rollback()
                            continue
                        processed += 1
                        rejected += 1
                        self.repo.update_ingestion_run(
                            run["id"],
                            expected_status="running",
                            cursor_page=chunk["page_end"],
                            processed_count=processed,
                            accepted_count=accepted,
                            rejected_count=rejected,
                        )
                        self.repo.commit()
                    except Exception:
                        self._best_effort_fail_extraction_claim(
                            chunk["id"],
                            attempt,
                        )
                        raise
                    continue

                # Hold the writer only while verifying ownership and persisting
                # local results. This makes result writes plus completion atomic.
                accepted_for_chunk = rejected_for_chunk = 0
                try:
                    self.repo.begin_immediate()
                    run_is_current = self.repo.ingestion_run_is_current(
                        run["id"],
                        expected_status="running",
                    )
                    claim_is_current = (
                        self.repo.rule_chunk_extraction_claim_is_current(
                            chunk["id"],
                            expected_attempt=attempt,
                        )
                    )
                    if not run_is_current or not claim_is_current:
                        self.repo.rollback()
                        if not run_is_current:
                            self._best_effort_fail_extraction_claim(
                                chunk["id"],
                                attempt,
                            )
                        continue
                    for candidate in candidates:
                        try:
                            rule, report = validator.validate(source_id, candidate)
                            status = validator.status_for(rule, report)
                            self.repo.store_rule_object(
                                source_id,
                                rule.model_dump(mode="json"),
                                status=status.value,
                                validation=report,
                            )
                            if report["passed"]:
                                accepted_for_chunk += 1
                            else:
                                rejected_for_chunk += 1
                        except sqlite3.Error:
                            raise
                        # Third-party validators can raise library-specific errors.
                        # A bad candidate is isolated and recorded without aborting its chunk.
                        except Exception as exc:  # noqa: BLE001
                            self.repo.record_rule_validation_issue(
                                source_id,
                                validation_layer="schema_or_validation",
                                error_text=str(exc),
                                chunk_id=chunk["id"],
                                run_id=run["id"],
                                candidate=candidate,
                            )
                            rejected_for_chunk += 1
                    if not self.repo.mark_rule_chunk(
                        chunk["id"],
                        extraction_status="completed",
                        expected_attempt=attempt,
                    ):
                        self.repo.rollback()
                        continue
                    processed += 1
                    accepted += accepted_for_chunk
                    rejected += rejected_for_chunk
                    self.repo.update_ingestion_run(
                        run["id"],
                        expected_status="running",
                        cursor_page=chunk["page_end"],
                        processed_count=processed,
                        accepted_count=accepted,
                        rejected_count=rejected,
                    )
                    self.repo.commit()
                except Exception:
                    self.repo.rollback()
                    self._best_effort_fail_extraction_claim(
                        chunk["id"],
                        attempt,
                    )
                    raise
            completed = self.repo.transition_ingestion_run(
                run["id"],
                expected_status="running",
                status="completed",
                processed_count=processed,
                accepted_count=accepted,
                rejected_count=rejected,
            )
            if completed is None:
                self.repo.rollback()
                return self.repo.update_ingestion_run(run["id"])
            self.repo.commit()
            return completed
        except Exception as exc:
            self.repo.rollback()
            self.repo.begin_immediate()
            failed = self.repo.transition_ingestion_run(
                run["id"],
                expected_status="running",
                status="failed",
                error_text=str(exc)[:2000],
            )
            if failed is not None:
                self.repo.commit()
            else:
                self.repo.rollback()
            raise

    def _best_effort_fail_extraction_claim(
        self,
        chunk_id: str,
        expected_attempt: int,
    ) -> None:
        """Release this attempt after an unexpected local persistence failure."""

        self.repo.rollback()
        try:
            self.repo.begin_immediate()
            if self.repo.mark_rule_chunk(
                chunk_id,
                extraction_status="failed",
                expected_attempt=expected_attempt,
            ):
                self.repo.commit()
            else:
                self.repo.rollback()
        except Exception:  # noqa: BLE001 - preserve the original system failure
            self.repo.rollback()

    async def query(
        self,
        *,
        ruleset_id: str,
        question: str,
        audience: str,
        top_k: int = 8,
    ) -> dict:
        if audience not in {"kp", "player"}:
            raise ValueError("Rule query audience must be kp or player")
        source = self.repo.find_rule_source(ruleset_id)
        backend = "minirag"
        chunk_ids: list[str] = []
        try:
            chunk_ids = await self._index(source).retrieve(question, top_k)
        except OriginalTextIndexUnavailableError:
            backend = "lexical_fallback"
        # MiniRAG is optional. Any backend-specific failure must preserve the
        # deterministic lexical fallback instead of breaking rule lookup.
        except Exception:  # noqa: BLE001
            backend = "lexical_fallback"
        chunks = []
        for chunk_id in chunk_ids:
            try:
                chunk = self.repo.get_rule_chunk(chunk_id)
            except KeyError:
                continue
            if chunk["source_id"] != source["id"]:
                continue
            if self._raw_chunk_visible_to(audience):
                chunks.append(chunk)
        if not chunks:
            backend = "lexical_fallback"
            chunks = self.repo.lexical_rule_chunks(source["id"], question, limit=top_k)
            chunks = [
                chunk for chunk in chunks if self._raw_chunk_visible_to(audience)
            ]
        rules = self.repo.search_validated_rules(
            source["id"], question, audience=audience, limit=top_k
        )
        return {
            "source": source,
            "retrieval_backend": backend,
            "chunks": [
                {
                    "id": chunk["id"],
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                    "chapter": chunk["chapter"],
                    "section": chunk["section"],
                    "text": chunk["text"],
                }
                for chunk in chunks
            ],
            "rules": rules,
        }

    @staticmethod
    def _raw_chunk_visible_to(audience: str) -> bool:
        """Keep raw source text closed until it has its own human review model.

        Existing ``rule_chunks.audience`` values were historically populated by
        extraction rather than a human decision. They therefore are not reliable
        authorization evidence. A KP may inspect the immutable source; players
        receive only rule objects whose exact hash, explicit audience, citations,
        and golden cases have all been approved.
        """

        return audience == "kp"

    def list_rules(
        self,
        source_id: str,
        *,
        status: str | None = None,
        rule_key: str | None = None,
    ) -> list[dict]:
        self.repo.get_rule_source(source_id)
        return self.repo.list_rule_objects(
            source_id,
            status=status,
            rule_key=rule_key,
        )

    def review_rule(
        self,
        rule_object_id: str,
        submission: RuleReviewSubmission,
        *,
        reviewer_member_id: str,
    ) -> dict:
        self.repo.begin_immediate()
        try:
            # Re-read under the writer transaction. Review-required is the only
            # mutable review state; validated and quarantined evidence is final.
            stored = self.repo.get_rule_object(rule_object_id)
            if stored["status"] != RuleStatus.REVIEW_REQUIRED.value:
                raise ValueError(
                    "Only review-required rules can be reviewed "
                    f"(current status: {stored['status']})"
                )
            validator = RuleValidator(self.repo)
            rule, validation = validator.validate(
                stored["source_id"],
                stored["object"],
            )
            review = {
                "decision": submission.decision,
                "reviewer_member_id": reviewer_member_id,
                "reviewed_at": datetime.now(UTC).isoformat(),
                "reviewed_object_hash": stored["object_hash"],
                "note": (submission.note or "").strip() or None,
            }
            validation["human_review"] = review

            if submission.decision == "rejected":
                validation["golden_tests"] = {
                    "passed": False,
                    "case_count": 0,
                    "passed_count": 0,
                    "comparison": "not_run_for_rejection",
                    "cases": [],
                }
                status = RuleStatus.QUARANTINED
            elif validation["passed"]:
                validation["golden_tests"] = evaluate_golden_cases(
                    rule,
                    submission.golden_cases,
                )
                status = (
                    RuleStatus.VALIDATED
                    if validation["golden_tests"]["passed"]
                    else RuleStatus.REVIEW_REQUIRED
                )
            else:
                validation["golden_tests"] = {
                    "passed": False,
                    "case_count": 0,
                    "passed_count": 0,
                    "comparison": "skipped_until_source_validation_passes",
                    "cases": [],
                }
                status = RuleValidator.status_for(rule, validation)

            reviewed = self.repo.update_rule_object_review(
                rule_object_id,
                status=status.value,
                validation=validation,
                expected_status=stored["status"],
                expected_object_hash=stored["object_hash"],
            )
            if (
                reviewed["status"] == RuleStatus.VALIDATED.value
                and not approval_allows_execution(reviewed)
            ):
                raise RuntimeError(
                    "Validated rule is missing executable approval evidence"
                )
            self.repo.commit()
            return reviewed
        except Exception:
            self.repo.rollback()
            raise

    def execute(
        self,
        *,
        ruleset_id: str,
        rule_key: str,
        inputs: dict[str, Any],
        audience: str,
    ) -> dict:
        if audience not in {"kp", "player"}:
            raise ValueError("Rule execution audience must be kp or player")
        source = self.repo.find_rule_source(ruleset_id)
        stored = self.repo.latest_validated_rule(source["id"], rule_key)
        if not approval_allows_execution(stored):
            raise KeyError(f"Validated executable rule not found: {rule_key}")
        stored_audience = stored["object"].get("audience")
        if audience != "kp" and stored_audience not in {"all", "player"}:
            raise KeyError(f"Validated rule not found: {rule_key}")
        rule = RuleObject.model_validate(stored["object"])
        return {
            "rule_key": rule.rule_key,
            "rule_object_id": stored["id"],
            "result": execute_rule(rule, inputs),
            "citations": [citation.model_dump(mode="json") for citation in rule.citations],
        }
