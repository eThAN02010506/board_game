from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_kp.application.rulebook_service import RulebookService
from ai_kp.core.db import connect, init_db
from ai_kp.core.repository import Repository
from ai_kp.infrastructure.document_process_sandbox import DocumentProcessPolicy
from ai_kp.infrastructure.knowledge.minirag import MiniRagOriginalIndex
from ai_kp.infrastructure.knowledge.rulebook_sandbox import (
    extract_rulebook_pdf_isolated,
)
from ai_kp.llm.openai_compatible import OpenAICompatibleClient
from ai_kp.rulebook.agent import RuleExtractionAgent
from ai_kp.rulebook.validation import RuleValidator


async def run(args: argparse.Namespace) -> None:
    pdf_path = Path(args.pdf).expanduser().resolve()
    connection = connect(Path(args.db).expanduser())
    init_db(connection)
    repo = Repository(connection)
    index_root = Path(args.index_root).expanduser()
    service = RulebookService(
        repo,
        extractor=lambda data, filename: extract_rulebook_pdf_isolated(
            data,
            filename,
            policy=DocumentProcessPolicy(),
        ),
        index_factory=lambda ruleset_id, source_id: MiniRagOriginalIndex(
            index_root,
            ruleset_id,
            source_id,
            384,
        ),
    )
    try:
        source = service.ingest_pdf(
            pdf_path.read_bytes(),
            pdf_path.name,
            ruleset_id=args.ruleset_id,
        )
        connection.commit()
        print(
            "INGEST",
            {key: source[key] for key in ("id", "source_hash", "page_count", "chunk_count")},
            flush=True,
        )
        if args.index:
            source = await service.index_source(source["id"])
            connection.commit()
            print("INDEX", {"status": source["status"], "chunks": source["chunk_count"]}, flush=True)
            retrieval = await service.query(
                ruleset_id=args.ruleset_id,
                question=args.question,
                audience="kp",
                top_k=5,
            )
            print(
                "RETRIEVE",
                {
                    "backend": retrieval["retrieval_backend"],
                    "pages": [chunk["page_start"] for chunk in retrieval["chunks"]],
                },
                flush=True,
            )
        if args.extract_page:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(f"{args.llm_base_url.rstrip('/')}/models")
                response.raise_for_status()
                model = response.json()["data"][0]["id"]
            chunk = next(
                item
                for item in repo.list_rule_chunks(source["id"])
                if item["page_start"] == args.extract_page
            )
            agent = RuleExtractionAgent(
                OpenAICompatibleClient(
                    args.llm_base_url,
                    args.llm_api_key,
                    model,
                    max_tokens=8192,
                )
            )
            candidates = await agent.extract(chunk, source["ruleset_id"])
            validator = RuleValidator(repo)
            outcomes = []
            for candidate in candidates:
                try:
                    rule, report = validator.validate(source["id"], candidate)
                    outcomes.append(
                        {
                            "rule_key": rule.rule_key,
                            "execution": rule.execution.kind,
                            "status": validator.status_for(rule, report).value,
                            "validation_passed": report["passed"],
                        }
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    outcomes.append({"status": "rejected", "error": str(exc)[:500]})
            print("MODEL", model, flush=True)
            print("EXTRACT", {"page": args.extract_page, "outcomes": outcomes}, flush=True)
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Real-case rulebook ingestion and retrieval")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--db", default="data/rulebook-realcase.sqlite3")
    parser.add_argument("--index-root", default="data/rag/realcase")
    parser.add_argument("--ruleset-id", default="coc7-keeper-cn-2002c")
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--extract-page", type=int)
    parser.add_argument("--question", default="单次伤害达到最大生命值一半时会发生什么？")
    parser.add_argument("--llm-base-url", default="http://192.168.1.97:8001/v1")
    parser.add_argument("--llm-api-key", default="local")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
