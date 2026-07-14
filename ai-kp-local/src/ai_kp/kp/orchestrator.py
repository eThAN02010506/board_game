import sqlite3

from ai_kp.kp.prompts import KP_SYSTEM_PROMPT, build_turn_prompt
from ai_kp.llm.base import ChatMessage, LlmClient
from ai_kp.memory.npc_candidates import NpcCandidateService
from ai_kp.memory.retrieval import MemoryRetriever


class KpOrchestrator:
    def __init__(self, connection: sqlite3.Connection, llm: LlmClient):
        self.connection = connection
        self.llm = llm

    async def handle_player_action(
        self,
        campaign_id: str,
        player_action: str,
        pc_id: str | None = None,
        location: str | None = None,
        profession_hint: str | None = None,
    ) -> str:
        memories = MemoryRetriever(self.connection).retrieve(
            player_action,
            campaign_id=campaign_id,
            pc_id=pc_id,
            visibility=("table", "kp"),
        )
        candidates = NpcCandidateService(self.connection).find_candidates(
            campaign_id=campaign_id,
            action_text=player_action,
            location=location,
            profession_hint=profession_hint,
        )
        prompt = build_turn_prompt(
            player_action=player_action,
            memories=[item.text for item in memories],
            npc_candidates=[f"{item.name}: {item.reason}" for item in candidates],
        )
        return await self.llm.complete(
            [
                ChatMessage(role="system", content=KP_SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompt),
            ]
        )

