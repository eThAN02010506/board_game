import sqlite3
from dataclasses import dataclass


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
        rows = self.connection.execute(
            """
            SELECT n.*, cn.relationship_score, cn.notes, cn.last_seen_time
            FROM campaign_npcs cn
            JOIN npcs n ON n.id = cn.npc_id
            WHERE cn.campaign_id = ?
            """,
            (campaign_id,),
        ).fetchall()
        action_lower = action_text.lower()
        candidates: list[NpcCandidate] = []
        for row in rows:
            score = int(row["relationship_score"] or 0)
            reasons: list[str] = []
            if profession_hint and row["profession"] and profession_hint in row["profession"]:
                score += 3
                reasons.append(f"profession matches {profession_hint}")
            if location and row["home_location"] and location in row["home_location"]:
                score += 2
                reasons.append(f"location matches {location}")
            if row["name"].lower() in action_lower:
                score += 4
                reasons.append("player named this NPC")
            if row["notes"] and any(word in row["notes"].lower() for word in action_lower.split()):
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
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[:limit]

