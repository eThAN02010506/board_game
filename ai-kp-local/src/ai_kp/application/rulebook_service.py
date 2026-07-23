from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.llm.base import LlmClient
from ai_kp.infrastructure.knowledge.minirag import (
    MiniRagOriginalIndex,
    MiniRagUnavailableError,
)
from ai_kp.infrastructure.knowledge.pdf_ingestion import extract_rulebook_pdf
from ai_kp.rule_authoring.agent import PROMPT_VERSION, RuleExtractionAgent
from ai_kp.rule_authoring.engine import execute_rule
from ai_kp.rule_authoring.models import RuleObject
from ai_kp.rule_authoring.validation import RuleValidator


class RulebookService:
    def __init__(
        self,
        repo: Repository,
        *,
        index_root: Path,
        embedding_dimensions: int = 384,
    ):
        self.repo = repo
        self.index_root = index_root
        self.embedding_dimensions = embedding_dimensions

    def ingest_pdf(
        self,
        data: bytes,
        filename: str,
        *,
        ruleset_id: str,
        title: str | None = None,
    ) -> dict:
        extracted = extract_rulebook_pdf(data, filename)
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

    def _index(self, source: dict) -> MiniRagOriginalIndex:
        return MiniRagOriginalIndex(
            self.index_root,
            source["ruleset_id"],
            source["id"],
            self.embedding_dimensions,
        )

    async def index_source(self, source_id: str) -> dict:
        source = self.repo.get_rule_source(source_id)
        chunks = self.repo.list_rule_chunks(source_id)
        run = self.repo.create_ingestion_run(source_id, stage="minirag_index")
        self.repo.set_rule_source_status(source_id, "indexing")
        try:
            mapping = await self._index(source).index_chunks(chunks)
            for chunk_id, mini_id in mapping.items():
                self.repo.mark_rule_chunk(chunk_id, minirag_doc_id=mini_id)
            self.repo.update_ingestion_run(
                run["id"],
                status="completed",
                processed_count=len(mapping),
                cursor_page=source["page_count"],
            )
            self.repo.set_rule_source_status(source_id, "ready")
        except Exception as exc:
            self.repo.update_ingestion_run(
                run["id"], status="failed", error_text=str(exc)[:2000]
            )
            self.repo.set_rule_source_status(source_id, "failed")
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
        agent = RuleExtractionAgent(llm)
        validator = RuleValidator(self.repo)
        processed = accepted = rejected = 0
        try:
            for chunk in chunks:
                self.repo.mark_rule_chunk(chunk["id"], extraction_status="processing")
                try:
                    candidates = await agent.extract(chunk, source["ruleset_id"])
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
                            if status.value == "validated":
                                accepted += 1
                            else:
                                rejected += 1
                        except Exception as exc:
                            self.repo.record_rule_validation_issue(
                                source_id,
                                validation_layer="schema_or_validation",
                                error_text=str(exc),
                                chunk_id=chunk["id"],
                                run_id=run["id"],
                                candidate=candidate,
                            )
                            rejected += 1
                    self.repo.mark_rule_chunk(chunk["id"], extraction_status="completed")
                except Exception as exc:
                    self.repo.mark_rule_chunk(chunk["id"], extraction_status="failed")
                    self.repo.record_rule_validation_issue(
                        source_id,
                        validation_layer="agent_output",
                        error_text=str(exc),
                        chunk_id=chunk["id"],
                        run_id=run["id"],
                    )
                    rejected += 1
                processed += 1
                self.repo.update_ingestion_run(
                    run["id"],
                    cursor_page=chunk["page_end"],
                    processed_count=processed,
                    accepted_count=accepted,
                    rejected_count=rejected,
                )
                self.repo.connection.commit()
            return self.repo.update_ingestion_run(
                run["id"],
                status="completed",
                processed_count=processed,
                accepted_count=accepted,
                rejected_count=rejected,
            )
        except Exception as exc:
            self.repo.update_ingestion_run(
                run["id"], status="failed", error_text=str(exc)[:2000]
            )
            raise

    async def query(
        self,
        *,
        ruleset_id: str,
        question: str,
        audience: str,
        top_k: int = 8,
    ) -> dict:
        source = self.repo.find_rule_source(ruleset_id)
        backend = "minirag"
        chunk_ids: list[str] = []
        try:
            chunk_ids = await self._index(source).retrieve(question, top_k)
        except MiniRagUnavailableError:
            backend = "lexical_fallback"
        except Exception:
            backend = "lexical_fallback"
        chunks = []
        for chunk_id in chunk_ids:
            try:
                chunk = self.repo.get_rule_chunk(chunk_id)
            except KeyError:
                continue
            if chunk["audience"] in {"all", audience}:
                chunks.append(chunk)
        if not chunks:
            backend = "lexical_fallback"
            chunks = self.repo.lexical_rule_chunks(source["id"], question, limit=top_k)
            chunks = [chunk for chunk in chunks if chunk["audience"] in {"all", audience}]
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

    def execute(
        self,
        *,
        ruleset_id: str,
        rule_key: str,
        inputs: dict[str, Any],
        audience: str,
    ) -> dict:
        source = self.repo.find_rule_source(ruleset_id)
        stored = self.repo.latest_validated_rule(source["id"], rule_key)
        rule = RuleObject.model_validate(stored["object"])
        if rule.audience.value not in {"all", audience}:
            raise KeyError(f"Validated rule not found: {rule_key}")
        return {
            "rule_key": rule.rule_key,
            "rule_object_id": stored["id"],
            "result": execute_rule(rule, inputs),
            "citations": [citation.model_dump(mode="json") for citation in rule.citations],
        }
