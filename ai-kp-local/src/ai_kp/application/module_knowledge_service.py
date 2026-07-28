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
            self.repo.mark_module_chunk_knowledge_status(chunk["id"], "processing")
            try:
                payloads = await agent.extract_from_chunk(chunk)
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
                        accepted += 1
                    except (KeyError, TypeError, ValueError):
                        rejected += 1
                self.repo.mark_module_chunk_knowledge_status(chunk["id"], "completed")
            except (KeyError, RuntimeError, TypeError, ValueError):
                self.repo.mark_module_chunk_knowledge_status(chunk["id"], "failed")
                rejected += 1
            processed += 1
            self.repo.commit()
        return {
            "module_id": module_id,
            "processed_count": processed,
            "accepted_count": accepted,
            "rejected_count": rejected,
            "prompt_version": PROMPT_VERSION,
            "model": model_name,
        }
