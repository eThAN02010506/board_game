"""Physically isolated MiniRAG adapter for original-text retrieval."""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any


MARKER = "RULE_CHUNK_ID"


class MiniRagUnavailableError(RuntimeError):
    pass


def _safe_working_dir(root: Path, ruleset_id: str, source_id: str) -> Path:
    safe_ruleset = re.sub(r"[^a-zA-Z0-9_.-]", "_", ruleset_id)
    safe_source = re.sub(r"[^a-zA-Z0-9_.-]", "_", source_id)
    return root / safe_ruleset / safe_source


class MiniRagOriginalIndex:
    """Use MiniRAG's local vector storage and naive context retrieval for source chunks."""

    def __init__(self, root: Path, ruleset_id: str, source_id: str, dimensions: int = 384):
        self.working_dir = _safe_working_dir(root, ruleset_id, source_id)
        self.dimensions = dimensions

    def _build(self):
        try:
            import numpy as np
            from minirag import MiniRAG
            from minirag.utils import EmbeddingFunc
        except Exception as exc:
            raise MiniRagUnavailableError(
                "MiniRAG dependencies are unavailable; install the project rulebook extra"
            ) from exc

        dimensions = self.dimensions

        async def embed(texts: list[str]):
            vectors = np.zeros((len(texts), dimensions), dtype=np.float32)
            for row, text in enumerate(texts):
                normalized = re.sub(r"\s+", "", text).casefold()
                features = normalized if len(normalized) < 3 else (
                    normalized[index : index + 3] for index in range(len(normalized) - 2)
                )
                for feature in features:
                    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
                    integer = int.from_bytes(digest, "big")
                    vectors[row, integer % dimensions] += 1 if integer & 1 else -1
                norm = np.linalg.norm(vectors[row])
                if norm:
                    vectors[row] /= norm
            return vectors

        async def unused_llm(_prompt: str, **_kwargs: Any) -> str:
            raise RuntimeError("Original-source retrieval must not call the language model")

        self.working_dir.mkdir(parents=True, exist_ok=True)
        return MiniRAG(
            working_dir=str(self.working_dir),
            embedding_func=EmbeddingFunc(
                embedding_dim=dimensions,
                max_token_size=8192,
                func=embed,
            ),
            llm_model_func=unused_llm,
            tiktoken_model_name="gpt-4o-mini",
            vector_db_storage_cls_kwargs={"cosine_better_than_threshold": -1.0},
        )

    async def index_chunks(self, chunks: list[dict[str, Any]]) -> dict[str, str]:
        rag = self._build()
        vector_rows: dict[str, dict[str, Any]] = {}
        mapping: dict[str, str] = {}
        for chunk in chunks:
            mini_id = "chunk-" + hashlib.sha256(chunk["id"].encode("utf-8")).hexdigest()[:32]
            mapping[chunk["id"]] = mini_id
            content = (
                f"[[{MARKER}:{chunk['id']}]][[PAGE:{chunk['page_start']}]]\n"
                f"{chunk.get('chapter') or ''}\n{chunk.get('section') or ''}\n{chunk['text']}"
            )
            vector_rows[mini_id] = {
                "content": content,
                "tokens": max(1, len(content) // 2),
                "chunk_order_index": chunk["order_index"],
                "full_doc_id": f"source-{chunk['source_id']}",
            }
        await asyncio.gather(
            rag.chunks_vdb.upsert(vector_rows),
            rag.text_chunks.upsert(vector_rows),
        )
        await asyncio.gather(
            rag.chunks_vdb.index_done_callback(),
            rag.text_chunks.index_done_callback(),
        )
        return mapping

    async def retrieve(self, query: str, top_k: int = 8) -> list[str]:
        rag = self._build()
        results = await rag.chunks_vdb.query(query, top_k=top_k)
        mini_ids = [str(result["id"]) for result in results]
        chunks = await rag.text_chunks.get_by_ids(mini_ids)
        chunk_ids: list[str] = []
        for chunk in chunks:
            if not chunk:
                continue
            match = re.search(rf"\[\[{MARKER}:([^\]]+)\]\]", chunk["content"])
            if match:
                chunk_ids.append(match.group(1))
        return list(dict.fromkeys(chunk_ids))
