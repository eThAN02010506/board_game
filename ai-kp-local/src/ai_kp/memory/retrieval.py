import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable


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
        allowed_visibility = tuple(visibility)
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
        rows = self.connection.execute(
            f"SELECT * FROM memories WHERE {where_clause} ORDER BY importance DESC, created_at DESC",
            params,
        ).fetchall()

        query_tokens = tokenize(query)
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
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[:limit]
