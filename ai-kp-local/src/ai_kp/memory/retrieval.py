import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable


TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")


def tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text)}


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
            score = lexical_score + (row["importance"] * 0.25)
            if score > 0:
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

