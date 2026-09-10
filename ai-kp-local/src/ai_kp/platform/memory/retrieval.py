"""Canonical system-neutral memory scoring and retrieval policy."""

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from math import log1p

from ai_kp.platform.memory.reranking import (
    MemoryRerankCandidate,
    MemoryRerankUnavailable,
    MemorySemanticRanker,
)

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
    score_components: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class _ScoredRow:
    row: sqlite3.Row
    tokens: frozenset[str]


class MemoryRetriever:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        semantic_ranker: MemorySemanticRanker | None = None,
        semantic_candidate_limit: int = 128,
    ):
        self.connection = connection
        self.semantic_ranker = semantic_ranker
        self.semantic_candidate_limit = max(1, semantic_candidate_limit)

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
        # The trigram index and the Unicode lexical scorer are independent first-
        # stage retrievers. Keep both candidate paths: restricting the SQL query to
        # FTS hits would silently lose synonyms/short Chinese fragments before a
        # future semantic reranker gets a chance to inspect them.
        rows = self.connection.execute(
            f"""
            SELECT rowid, * FROM memories
            WHERE {where_clause}
            ORDER BY importance DESC, created_at DESC, id ASC
            """,
            params,
        ).fetchall()

        ranked = self._rank(rows, query_tokens)
        id_by_rowid = {int(row["rowid"]): str(row["id"]) for row in rows}
        fts_ids = tuple(
            id_by_rowid[rowid] for rowid in candidate_rowids if rowid in id_by_rowid
        )
        semantic_ids = self._semantic_ranking(query, rows)
        retrieval_only_ids = tuple(dict.fromkeys((*fts_ids, *semantic_ids)))
        if retrieval_only_ids:
            ranked_by_id = {item.id: item for item in ranked}
            rows_by_id = {str(row["id"]): row for row in rows}
            for memory_id in retrieval_only_ids:
                if memory_id in ranked_by_id:
                    continue
                row = rows_by_id[memory_id]
                signals = tuple(
                    (name, 1.0)
                    for name, ids in (
                        ("fts_only", fts_ids),
                        ("semantic_only", semantic_ids),
                    )
                    if memory_id in ids
                )
                ranked_by_id[memory_id] = RetrievedMemory(
                    id=memory_id,
                    text=str(row["text"]),
                    scope=str(row["scope"]),
                    importance=int(row["importance"]),
                    visibility=str(row["visibility"]),
                    score=0.0,
                    score_components=signals,
                )
            ranked = list(ranked_by_id.values())
        ranked = self._fuse_rankings(ranked, fts_ids, semantic_ids)
        return ranked[:limit]

    def _semantic_ranking(
        self,
        query: str,
        rows: Iterable[sqlite3.Row],
    ) -> tuple[str, ...]:
        if self.semantic_ranker is None:
            return ()
        candidates = tuple(
            MemoryRerankCandidate(
                memory_id=str(row["id"]),
                text=str(row["text"]),
                scope=str(row["scope"]),
                importance=int(row["importance"]),
            )
            for row in tuple(rows)[: self.semantic_candidate_limit]
        )
        try:
            ranked_ids = self.semantic_ranker.rank(query, candidates)
        except MemoryRerankUnavailable:
            return ()
        allowed = {candidate.memory_id for candidate in candidates}
        return tuple(
            dict.fromkeys(memory_id for memory_id in ranked_ids if memory_id in allowed)
        )

    @staticmethod
    def _rank(rows: Iterable[sqlite3.Row], query_tokens: set[str]) -> list[RetrievedMemory]:
        # Rank only after visibility/campaign/character filtering. IDF is derived
        # from this authorized candidate set so secret rows cannot influence even
        # the score of a visible result.
        candidates = tuple(
            _ScoredRow(row=row, tokens=frozenset(tokenize(row["text"])))
            for row in rows
        )
        if not candidates:
            return []
        document_frequency = {
            token: sum(token in candidate.tokens for candidate in candidates)
            for token in query_tokens
        }
        token_weights = {
            token: 1.0 + log1p(len(candidates) / max(document_frequency[token], 1))
            for token in query_tokens
        }
        total_query_weight = sum(token_weights.values()) or 1.0
        ranked: list[RetrievedMemory] = []
        for candidate in candidates:
            overlap = query_tokens & candidate.tokens
            if not overlap:
                continue
            weighted_overlap = sum(token_weights[token] for token in overlap)
            query_coverage = weighted_overlap / total_query_weight
            importance_bonus = min(max(int(candidate.row["importance"]), 0), 5) * 0.15
            score = weighted_overlap + (query_coverage * 2.0) + importance_bonus
            components = (
                ("weighted_lexical_overlap", round(weighted_overlap, 6)),
                ("query_coverage", round(query_coverage, 6)),
                ("importance_bonus", round(importance_bonus, 6)),
            )
            ranked.append(
                RetrievedMemory(
                    id=candidate.row["id"],
                    text=candidate.row["text"],
                    scope=candidate.row["scope"],
                    importance=candidate.row["importance"],
                    visibility=candidate.row["visibility"],
                    score=score,
                    score_components=components,
                )
            )
        return ranked

    @staticmethod
    def _fuse_rankings(
        lexical: list[RetrievedMemory],
        fts_ids: tuple[str, ...],
        semantic_ids: tuple[str, ...] = (),
        *,
        rank_constant: int = 60,
    ) -> list[RetrievedMemory]:
        """Fuse Unicode lexical and FTS5-trigram ranks without score calibration.

        Reciprocal Rank Fusion is deliberately used instead of another collection
        of opaque +5/+10 bonuses. A future dense or Agent reranker can become a
        third ranked input without changing permissions or the memory DTO.
        """

        lexical_items = [
            item
            for item in lexical
            if not {"fts_only", "semantic_only"} & dict(item.score_components).keys()
        ]
        lexical_items.sort(key=lambda item: (-item.score, item.id))
        lexical_ranks = {
            item.id: rank for rank, item in enumerate(lexical_items, start=1)
        }
        fts_ranks = {item_id: rank for rank, item_id in enumerate(fts_ids, start=1)}
        semantic_ranks = {
            item_id: rank for rank, item_id in enumerate(semantic_ids, start=1)
        }
        fused: list[RetrievedMemory] = []
        for item in lexical:
            lexical_rrf = (
                1.0 / (rank_constant + lexical_ranks[item.id])
                if item.id in lexical_ranks
                else 0.0
            )
            fts_rrf = (
                1.0 / (rank_constant + fts_ranks[item.id])
                if item.id in fts_ranks
                else 0.0
            )
            semantic_rrf = (
                1.0 / (rank_constant + semantic_ranks[item.id])
                if item.id in semantic_ranks
                else 0.0
            )
            fused_score = lexical_rrf + fts_rrf + semantic_rrf
            fused.append(
                RetrievedMemory(
                    id=item.id,
                    text=item.text,
                    scope=item.scope,
                    importance=item.importance,
                    visibility=item.visibility,
                    score=fused_score,
                    score_components=(
                        *item.score_components,
                        ("lexical_rank", float(lexical_ranks.get(item.id, 0))),
                        ("fts_rank", float(fts_ranks.get(item.id, 0))),
                        ("semantic_rank", float(semantic_ranks.get(item.id, 0))),
                        ("rrf_score", round(fused_score, 9)),
                    ),
                )
            )
        fused.sort(key=lambda item: (-item.score, item.id))
        return fused

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
