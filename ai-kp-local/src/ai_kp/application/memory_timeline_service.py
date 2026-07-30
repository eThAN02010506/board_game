"""Application boundary for evidence-backed memory timelines."""

from dataclasses import dataclass
from typing import Literal

from ai_kp.application.ports.repositories import WorldStore

MemoryClassification = Literal["major", "side", "npc", "clue", "other"]


@dataclass(frozen=True)
class CurateMemoryCommand:
    classification: MemoryClassification
    importance: int
    hidden: bool
    reason: str
    expected_head_action_id: str | None


class MemoryTimelineService:
    def __init__(self, repo: WorldStore):
        self.repo = repo

    def list_timeline(
        self,
        campaign_id: str,
        *,
        pc_id: str | None,
        view: str,
        classification: str | None,
        query: str | None,
        include_hidden: bool,
        limit: int,
    ) -> list[dict]:
        return self.repo.list_memory_timeline(
            campaign_id,
            pc_id=pc_id,
            view=view,
            classification=classification,
            query=query,
            include_hidden=include_hidden,
            limit=limit,
        )

    def curate(
        self,
        campaign_id: str,
        memory_id: str,
        *,
        session_id: str,
        member_id: str,
        command: CurateMemoryCommand,
    ) -> dict:
        action = self.repo.append_memory_curation(
            campaign_id,
            memory_id,
            classification=command.classification,
            importance=command.importance,
            hidden=command.hidden,
            reason=command.reason,
            expected_head_action_id=command.expected_head_action_id,
            created_by_member_id=member_id,
        )
        self.repo.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience="kp",
            event_type="memory.curated",
            resource_type="memory",
            resource_id=memory_id,
            payload={"curation_action_id": action["id"]},
        )
        return action

