"""Application use cases for source-bound module knowledge extraction."""

from __future__ import annotations

from ai_kp.application.ports.repositories import ModuleKnowledgeStore
from ai_kp.platform.modules.knowledge import validate_module_candidate
from ai_kp.platform.modules.knowledge_agent import (
    PROMPT_VERSION,
    ModuleKnowledgeAgent,
)
from ai_kp.platform.ports.llm import LlmClient


class ModuleKnowledgeService:
    def __init__(self, repo: ModuleKnowledgeStore):
        self.repo = repo

    def create_manual_candidate(self, module_id: str, payload: dict) -> dict:
        candidate, object_hash = validate_module_candidate(self.repo, module_id, payload)
        return self.repo.store_module_knowledge_candidate(
            module_id,
            candidate,
            object_hash=object_hash,
            created_by="human_kp",
            source_model=None,
            prompt_version=None,
        )

    def review_candidate(
        self,
        candidate_id: str,
        *,
        decision: str,
        member_id: str | None,
        note: str | None,
    ) -> dict:
        """Review against current sources, never the ACL captured at extraction time."""

        return self.repo.review_module_knowledge_candidate(
            candidate_id,
            decision=decision,
            member_id=member_id,
            note=note,
        )

    async def extract(
        self,
        module_id: str,
        llm: LlmClient,
        *,
        model_name: str,
        limit: int = 5,
        retry_failed: bool = False,
    ) -> dict:
        if retry_failed:
            self.repo.reset_failed_module_chunks(module_id)
        chunks = self.repo.list_module_chunks_for_knowledge(
            module_id,
            status="pending",
            limit=limit,
        )
        agent = ModuleKnowledgeAgent(llm)
        processed = accepted = rejected = 0
        for chunk in chunks:
            attempt = self.repo.claim_module_chunk_for_knowledge(chunk["id"])
            # Publish the claim before the external model call. SQLite allows one
            # writer, so no write transaction may span this await.
            try:
                self.repo.commit()
            except Exception:
                if attempt is not None:
                    self._best_effort_fail_claim(chunk["id"], attempt)
                raise
            if attempt is None:
                continue
            try:
                payloads = await agent.extract_from_chunk(chunk)
            except (KeyError, RuntimeError, TypeError, ValueError):
                try:
                    self.repo.begin_immediate()
                    if not self.repo.module_chunk_knowledge_claim_is_current(
                        chunk["id"],
                        expected_attempt=attempt,
                    ):
                        self.repo.rollback()
                        continue
                    if not self.repo.mark_module_chunk_knowledge_status(
                        chunk["id"],
                        "failed",
                        expected_attempt=attempt,
                    ):
                        self.repo.rollback()
                        continue
                    self.repo.commit()
                except Exception:
                    self._best_effort_fail_claim(chunk["id"], attempt)
                    raise
                processed += 1
                rejected += 1
                continue

            # Claim verification, local result writes, and completion are one
            # write transaction. Recovery or a newer attempt can win before
            # this boundary, but never during it.
            accepted_for_chunk = rejected_for_chunk = 0
            try:
                self.repo.begin_immediate()
                if not self.repo.module_chunk_knowledge_claim_is_current(
                    chunk["id"],
                    expected_attempt=attempt,
                ):
                    self.repo.rollback()
                    continue
                for payload in payloads:
                    try:
                        candidate, object_hash = validate_module_candidate(
                            self.repo,
                            module_id,
                            payload,
                        )
                        self.repo.store_module_knowledge_candidate(
                            module_id,
                            candidate,
                            object_hash=object_hash,
                            created_by="ai",
                            source_model=model_name,
                            prompt_version=PROMPT_VERSION,
                        )
                        accepted_for_chunk += 1
                    except (KeyError, TypeError, ValueError):
                        rejected_for_chunk += 1
                if not self.repo.mark_module_chunk_knowledge_status(
                    chunk["id"],
                    "completed",
                    expected_attempt=attempt,
                ):
                    self.repo.rollback()
                    continue
                self.repo.commit()
            except Exception:
                self.repo.rollback()
                self._best_effort_fail_claim(chunk["id"], attempt)
                raise
            processed += 1
            accepted += accepted_for_chunk
            rejected += rejected_for_chunk
        return {
            "module_id": module_id,
            "processed_count": processed,
            "accepted_count": accepted,
            "rejected_count": rejected,
            "prompt_version": PROMPT_VERSION,
            "model": model_name,
        }

    def _best_effort_fail_claim(self, chunk_id: str, expected_attempt: int) -> None:
        """Release this attempt after an unexpected local persistence failure."""

        self.repo.rollback()
        try:
            self.repo.begin_immediate()
            if self.repo.mark_module_chunk_knowledge_status(
                chunk_id,
                "failed",
                expected_attempt=expected_attempt,
            ):
                self.repo.commit()
            else:
                self.repo.rollback()
        except Exception:  # noqa: BLE001 - preserve the original system failure
            self.repo.rollback()
