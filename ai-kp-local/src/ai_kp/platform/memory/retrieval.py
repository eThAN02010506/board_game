"""Canonical system-neutral memory scoring and retrieval policy."""

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
CJK_SEQUENCE_RE = re.compile(r"[\u3400-\u9fff]+")


def tokenize(text: str) -> set[str]:
    tokens = {token.lower() for token in WORD_RE.findall(text)}
    for sequence in CJK_SEQUENCE_RE.findall(text):
        if len(sequence) == 1:
            tokens.add(sequence)
            continue
        tokens.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


@dataclass(frozen=True)
class RetrievedMemory:
    id: str
    text: str
    scope: str
    importance: int
    visibility: str
    score: float


class MemoryRetriever:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def retrieve(
        self,
        query: str,
        campaign_id: str | None = None,
        pc_id: str | None = None,
        visibility: Iterable[str] = ("table", "kp"),
        limit: int = 8,
    ) -> list[RetrievedMemory]:
        if limit <= 0:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        if isinstance(visibility, str):
            allowed_visibility = (visibility,)
        else:
            allowed_visibility = tuple(dict.fromkeys(visibility))
        if not allowed_visibility:
            return []

        placeholders = ",".join("?" for _ in allowed_visibility)
        params: list[object] = [*allowed_visibility]
        filters = [f"visibility IN ({placeholders})"]
        if campaign_id:
            filters.append("(campaign_id = ? OR campaign_id IS NULL)")
            params.append(campaign_id)
        if pc_id:
            filters.append("(pc_id = ? OR pc_id IS NULL)")
            params.append(pc_id)
        where_clause = " AND ".join(filters)
        candidate_rowids = self._fts_candidate_rowids(
            query,
            where_clause=where_clause,
            params=params,
            limit=max(limit * 8, 64),
        )
        candidate_filter = ""
        candidate_params: list[object] = []
        if candidate_rowids:
            candidate_filter = (
                f" AND rowid IN ({','.join('?' for _ in candidate_rowids)})"
            )
            candidate_params.extend(candidate_rowids)
        rows = self.connection.execute(
            f"""
            SELECT * FROM memories
            WHERE {where_clause}{candidate_filter}
            ORDER BY importance DESC, created_at DESC, id ASC
            """,
            [*params, *candidate_params],
        ).fetchall()

        ranked = self._rank(rows, query_tokens)
        if candidate_rowids and len(ranked) < limit:
            # Trigram search intentionally favors precise substring candidates.
            # Preserve recall for short or non-contiguous Chinese queries by
            # merging the established lexical scan when the candidate set is thin.
            fallback_rows = self.connection.execute(
                f"""
                SELECT * FROM memories
                WHERE {where_clause}
                ORDER BY importance DESC, created_at DESC, id ASC
                """,
                params,
            ).fetchall()
            by_id = {item.id: item for item in ranked}
            by_id.update(
                {
                    item.id: item
                    for item in self._rank(fallback_rows, query_tokens)
                    if item.id not in by_id
                }
            )
            ranked = list(by_id.values())
        ranked.sort(key=lambda item: (-item.score, item.id))
        return ranked[:limit]

    @staticmethod
    def _rank(rows: Iterable[sqlite3.Row], query_tokens: set[str]) -> list[RetrievedMemory]:
        ranked: list[RetrievedMemory] = []
        for row in rows:
            text_tokens = tokenize(row["text"])
            lexical_score = len(query_tokens & text_tokens)
            if lexical_score == 0:
                continue
            score = lexical_score + (row["importance"] * 0.25)
            ranked.append(
                RetrievedMemory(
                    id=row["id"],
                    text=row["text"],
                    scope=row["scope"],
                    importance=row["importance"],
                    visibility=row["visibility"],
                    score=score,
                )
            )
        return ranked

    def _fts_candidate_rowids(
        self,
        query: str,
        *,
        where_clause: str,
        params: list[object],
        limit: int,
    ) -> list[int]:
        terms = _trigram_query_terms(query)
        if not terms:
            return []
        match_query = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        try:
            rows = self.connection.execute(
                f"""
                SELECT rowid
                FROM memories_fts
                WHERE memories_fts MATCH ? AND {where_clause}
                ORDER BY bm25(memories_fts), rowid
                LIMIT ?
                """,
                [match_query, *params, limit],
            ).fetchall()
        except sqlite3.OperationalError:
            # Older imported databases may be opened read-only before migrations,
            # or a custom SQLite build may omit FTS5. Lexical retrieval remains valid.
            return []
        return [int(row["rowid"]) for row in rows]


def _trigram_query_terms(text: str) -> tuple[str, ...]:
    terms: list[str] = []
    for token in WORD_RE.findall(text.lower()):
        if len(token) >= 3:
            terms.append(token)
    for sequence in CJK_SEQUENCE_RE.findall(text):
        if len(sequence) >= 3:
            terms.extend(sequence[index : index + 3] for index in range(len(sequence) - 2))
    return tuple(dict.fromkeys(terms))
