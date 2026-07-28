"""Canonical policy for selecting previously encountered NPC candidates."""

import sqlite3
from dataclasses import dataclass

from ai_kp.platform.memory.retrieval import tokenize


@dataclass(frozen=True)
class NpcCandidate:
    npc_id: str
    name: str
    reason: str
    score: int


class NpcCandidateService:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def find_candidates(
        self,
        campaign_id: str,
        action_text: str,
        location: str | None = None,
        profession_hint: str | None = None,
        limit: int = 3,
    ) -> list[NpcCandidate]:
        if limit <= 0:
            return []

        rows = self.connection.execute(
            """
            SELECT n.*, cn.relationship_score, cn.notes, cn.last_seen_time
            FROM campaign_npcs cn
            JOIN npcs n ON n.id = cn.npc_id
            WHERE cn.campaign_id = ?
            """,
            (campaign_id,),
        ).fetchall()
        action_normalized = action_text.casefold()
        action_tokens = tokenize(action_text)
        location_normalized = location.strip().casefold() if location else ""
        profession_normalized = profession_hint.strip().casefold() if profession_hint else ""
        candidates: list[NpcCandidate] = []
        for row in rows:
            score = int(row["relationship_score"] or 0)
            reasons: list[str] = []
            if (
                profession_normalized
                and row["profession"]
                and profession_normalized in row["profession"].casefold()
            ):
                score += 3
                reasons.append(f"profession matches {profession_hint.strip()}")
            if (
                location_normalized
                and row["home_location"]
                and location_normalized in row["home_location"].casefold()
            ):
                score += 2
                reasons.append(f"location matches {location.strip()}")
            if row["name"].casefold() in action_normalized:
                score += 4
                reasons.append("player named this NPC")
            if row["notes"] and action_tokens & tokenize(row["notes"]):
                score += 1
                reasons.append("past notes overlap with action")
            if score > 0:
                candidates.append(
                    NpcCandidate(
                        npc_id=row["id"],
                        name=row["name"],
                        reason="; ".join(reasons) or "prior relationship",
                        score=score,
                    )
                )
        candidates.sort(key=lambda item: (-item.score, item.name.casefold(), item.npc_id))
        return candidates[:limit]
