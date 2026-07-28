"""SQLite adapter for module search, derived image analysis, and KP review."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.memory.retrieval import tokenize
from ai_kp.platform.modules.knowledge import ModuleKnowledgeCandidate

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")


class ModuleKnowledgeRepository(SQLiteRepository):
    def get_module_chunk(self, chunk_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM module_chunks WHERE id = ?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Module chunk not found: {chunk_id}")
        return row_to_dict(row)

    def update_module_section_scope(
        self,
        module_id: str,
        *,
        title: str,
        visibility: str,
        spoiler_tag: str | None,
    ) -> dict:
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("Section title cannot be blank")
        cursor = self.connection.execute(
            """
            UPDATE module_chunks
            SET visibility = ?, spoiler_tag = ?
            WHERE module_id = ? AND title = ?
            """,
            (visibility, spoiler_tag, module_id, normalized_title),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Module section not found: {normalized_title}")
        asset_cursor = self.connection.execute(
            """
            UPDATE module_assets
            SET visibility = ?, spoiler_tag = ?
            WHERE module_id = ? AND nearby_heading = ?
            """,
            (visibility, spoiler_tag, module_id, normalized_title),
        )
        return {
            "module_id": module_id,
            "title": normalized_title,
            "visibility": visibility,
            "spoiler_tag": spoiler_tag,
            "chunk_count": cursor.rowcount,
            "asset_count": asset_cursor.rowcount,
        }

    def update_module_asset_analysis(
        self,
        asset_id: str,
        *,
        status: str,
        model: str,
        prompt_version: str,
        ocr_text: str | None = None,
        visual_summary: str | None = None,
        error_text: str | None = None,
    ) -> dict:
        self.get_module_asset(asset_id)
        self.connection.execute(
            """
            UPDATE module_assets
            SET analysis_status = ?,
                ocr_text = CASE WHEN ? IS NULL THEN ocr_text ELSE ? END,
                visual_summary = CASE WHEN ? IS NULL THEN visual_summary ELSE ? END,
                analysis_model = ?,
                analysis_error = ?,
                analysis_prompt_version = ?,
                analyzed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                status,
                ocr_text,
                ocr_text,
                visual_summary,
                visual_summary,
                model,
                error_text,
                prompt_version,
                asset_id,
            ),
        )
        return self.get_module_asset(asset_id)

    def list_module_chunks_for_knowledge(
        self,
        module_id: str,
        *,
        status: str = "pending",
        limit: int = 5,
    ) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM module_chunks
            WHERE module_id = ? AND knowledge_status = ?
            ORDER BY order_index
            LIMIT ?
            """,
            (module_id, status, limit),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def mark_module_chunk_knowledge_status(self, chunk_id: str, status: str) -> None:
        self.connection.execute(
            "UPDATE module_chunks SET knowledge_status = ? WHERE id = ?",
            (status, chunk_id),
        )

    def reset_failed_module_chunks(self, module_id: str) -> int:
        cursor = self.connection.execute(
            """
            UPDATE module_chunks SET knowledge_status = 'pending'
            WHERE module_id = ? AND knowledge_status = 'failed'
            """,
            (module_id,),
        )
        return cursor.rowcount

    def store_module_knowledge_candidate(
        self,
        module_id: str,
        candidate: ModuleKnowledgeCandidate,
        *,
        object_hash: str,
        created_by: str,
        source_model: str | None,
        prompt_version: str | None,
    ) -> dict:
        candidate_id = new_id("modknow")
        self.connection.execute(
            """
            INSERT INTO module_knowledge_candidates
              (id, module_id, kind, title, statement, rationale, confidence,
               visibility, spoiler_tag, object_hash, created_by, source_model,
               prompt_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(module_id, object_hash) DO NOTHING
            """,
            (
                candidate_id,
                module_id,
                candidate.kind,
                candidate.title,
                candidate.statement,
                candidate.rationale,
                candidate.confidence,
                candidate.visibility,
                candidate.spoiler_tag,
                object_hash,
                created_by,
                source_model,
                prompt_version,
            ),
        )
        row = self.connection.execute(
            """
            SELECT id FROM module_knowledge_candidates
            WHERE module_id = ? AND object_hash = ?
            """,
            (module_id, object_hash),
        ).fetchone()
        if row is None:
            raise RuntimeError("Module knowledge candidate was not stored")
        stored_id = str(row["id"])
        for citation in candidate.citations:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO module_knowledge_citations
                  (candidate_id, chunk_id, asset_id, evidence_text, evidence_hash,
                   source_locator)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    stored_id,
                    citation.chunk_id,
                    citation.asset_id,
                    citation.evidence_text,
                    citation.evidence_hash,
                    citation.source_locator,
                ),
            )
        return self.get_module_knowledge_candidate(stored_id)

    def get_module_knowledge_candidate(self, candidate_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM module_knowledge_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Module knowledge candidate not found: {candidate_id}")
        result = row_to_dict(row)
        citations = self.connection.execute(
            """
            SELECT * FROM module_knowledge_citations
            WHERE candidate_id = ?
            ORDER BY source_locator, evidence_hash
            """,
            (candidate_id,),
        ).fetchall()
        result["citations"] = [row_to_dict(item) for item in citations]
        return result

    def list_module_knowledge_candidates(
        self,
        module_id: str,
        *,
        status: str | None = None,
    ) -> list[dict]:
        if status:
            rows = self.connection.execute(
                """
                SELECT id FROM module_knowledge_candidates
                WHERE module_id = ? AND status = ?
                ORDER BY created_at DESC, id
                """,
                (module_id, status),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT id FROM module_knowledge_candidates
                WHERE module_id = ?
                ORDER BY created_at DESC, id
                """,
                (module_id,),
            ).fetchall()
        return [self.get_module_knowledge_candidate(str(row["id"])) for row in rows]

    def review_module_knowledge_candidate(
        self,
        candidate_id: str,
        *,
        decision: str,
        member_id: str,
        note: str | None,
    ) -> dict:
        cursor = self.connection.execute(
            """
            UPDATE module_knowledge_candidates
            SET status = ?, review_note = ?, reviewed_by_member_id = ?,
                reviewed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (decision, note, member_id, candidate_id),
        )
        if cursor.rowcount != 1:
            candidate = self.get_module_knowledge_candidate(candidate_id)
            raise ValueError(
                f"Only pending candidates can be reviewed; current status is "
                f"{candidate['status']}"
            )
        return self.get_module_knowledge_candidate(candidate_id)

    def search_module(
        self,
        module_id: str,
        query: str,
        *,
        allowed_visibility: Iterable[str],
        spoiler_tags: tuple[str, ...] | None,
        limit: int = 12,
    ) -> list[dict]:
        visibility = tuple(dict.fromkeys(allowed_visibility))
        if not visibility or limit <= 0:
            return []
        placeholders = ",".join("?" for _ in visibility)
        filters = ["module_id = ?", f"visibility IN ({placeholders})"]
        params: list[object] = [module_id, *visibility]
        if spoiler_tags is not None:
            if spoiler_tags:
                spoiler_placeholders = ",".join("?" for _ in spoiler_tags)
                filters.append(
                    f"(spoiler_tag IS NULL OR spoiler_tag IN ({spoiler_placeholders}))"
                )
                params.extend(spoiler_tags)
            else:
                filters.append("spoiler_tag IS NULL")
        where = " AND ".join(filters)
        terms = _trigram_terms(query)
        rows: list[sqlite3.Row] = []
        if terms:
            match_query = " OR ".join(
                f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms
            )
            try:
                rows = self.connection.execute(
                    f"""
                    SELECT source_type, source_id, module_id, visibility, spoiler_tag,
                           source_locator, title, text, bm25(module_search_fts) AS score
                    FROM module_search_fts
                    WHERE module_search_fts MATCH ? AND {where}
                    ORDER BY score, source_type, source_id
                    LIMIT ?
                    """,
                    [match_query, *params, limit],
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            candidates = self.connection.execute(
                f"""
                SELECT source_type, source_id, module_id, visibility, spoiler_tag,
                       source_locator, title, text
                FROM module_search_fts
                WHERE {where}
                """,
                params,
            ).fetchall()
            query_tokens = tokenize(query)
            scored = []
            for row in candidates:
                overlap = len(query_tokens & tokenize(f"{row['title']} {row['text']}"))
                if overlap:
                    scored.append((overlap, row))
            scored.sort(key=lambda item: (-item[0], item[1]["source_id"]))
            rows = [item[1] for item in scored[:limit]]
        return [
            {
                "source_type": row["source_type"],
                "source_id": row["source_id"],
                "module_id": row["module_id"],
                "visibility": row["visibility"],
                "spoiler_tag": row["spoiler_tag"],
                "source_locator": row["source_locator"],
                "title": row["title"],
                "text": str(row["text"])[:2000],
            }
            for row in rows
        ]


def _trigram_terms(text: str) -> tuple[str, ...]:
    terms: list[str] = []
    for word in _WORD_RE.findall(text.casefold()):
        if len(word) >= 3:
            terms.append(word)
    for sequence in _CJK_RE.findall(text):
        if len(sequence) >= 3:
            terms.extend(sequence[index : index + 3] for index in range(len(sequence) - 2))
    return tuple(dict.fromkeys(terms))
