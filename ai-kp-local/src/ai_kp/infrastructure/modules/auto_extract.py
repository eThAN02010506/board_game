"""Post-import automatic knowledge extraction and entity confirmation.

After a module document is imported, extract knowledge candidates from the
parsed chunks and auto-confirm the entities they establish (NPCs, locations,
clues). Extraction attribution groups multiple statements about the same world
entity into one confirmed entity (for example, attributes and events that name
the same character collapse into one NPC), so the AI can narrate from confirmed
entities instead of guessing from raw text chunks.

Extraction is best-effort and non-blocking: a model failure must never make
an already-completed import appear failed.
"""

from __future__ import annotations

import asyncio
import sqlite3

from ai_kp.application.module_entity_materialization_service import (
    ModuleEntityMaterializationService,
)
from ai_kp.application.module_knowledge_service import ModuleKnowledgeService
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from ai_kp.platform.ports.llm import LlmClient

EXTRACT_BATCH_LIMIT = 5


def auto_extract_and_confirm_entities(
    connection: sqlite3.Connection,
    module_id: str,
    *,
    settings: Settings,
    auto_approve: bool = False,
) -> None:
    """Extract knowledge candidates and confirm entities for a fresh module.

    Runs synchronously from the import worker thread via ``asyncio.run``.
    All failures are swallowed: the import is already complete.
    """
    try:
        repo = Repository(connection)
        llm = OpenAICompatibleClient(
            settings.llm_base_url,
            settings.llm_api_key,
            settings.llm_model,
        )
        asyncio.run(
            _extract_and_confirm(
                repo,
                module_id,
                llm,
                model_name=settings.llm_model,
                auto_approve=auto_approve,
            )
        )
    except Exception:  # noqa: BLE001 - best-effort background extraction
        connection.rollback()


async def _extract_and_confirm(
    repo: Repository,
    module_id: str,
    llm: LlmClient,
    *,
    model_name: str,
    auto_approve: bool,
) -> None:
    knowledge = ModuleKnowledgeService(repo)
    # Extract up to a bounded batch of pending chunks. A full book can have
    # many chunks; leave the rest pending for later manual or repeated runs.
    result = await knowledge.extract(
        module_id,
        llm,
        model_name=model_name,
        limit=EXTRACT_BATCH_LIMIT,
    )
    candidate_ids = tuple(
        str(candidate_id)
        for candidate_id in result.get("accepted_candidate_ids") or ()
    )
    if not auto_approve or not candidate_ids:
        return
    ModuleEntityMaterializationService(repo).review_and_materialize(
        module_id,
        candidate_ids,
        member_id=None,
        note="auto-confirmed on import (ai_kp)",
    )


__all__ = ["auto_extract_and_confirm_entities"]
