from collections.abc import Iterator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_kp.api.schemas import (
    CampaignCreate,
    CampaignNpcLink,
    EventCreate,
    KpTurnRequest,
    MemoryCreate,
    ModuleImport,
    NpcCreate,
    PcCreate,
)
from ai_kp.core.config import Settings, get_settings
from ai_kp.core.db import connect, init_db
from ai_kp.core.repository import Repository
from ai_kp.kp.orchestrator import KpOrchestrator
from ai_kp.llm.openai_compatible import OpenAICompatibleClient
from ai_kp.memory.npc_candidates import NpcCandidateService
from ai_kp.memory.retrieval import MemoryRetriever
from ai_kp.modules.ingestion import chunk_plaintext_module


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AI KP Local", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_repo() -> Iterator[Repository]:
        connection = connect(settings.db_path)
        init_db(connection)
        try:
            yield Repository(connection)
            connection.commit()
        finally:
            connection.close()

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/campaigns")
    def list_campaigns(repo: Repository = Depends(get_repo)) -> list[dict]:
        return repo.list_campaigns()

    @app.post("/campaigns")
    def create_campaign(payload: CampaignCreate, repo: Repository = Depends(get_repo)) -> dict:
        return repo.create_campaign(payload.title, payload.system, payload.current_time)

    @app.post("/campaigns/{campaign_id}/pcs")
    def create_pc(campaign_id: str, payload: PcCreate, repo: Repository = Depends(get_repo)) -> dict:
        return repo.create_pc(campaign_id, payload.name, payload.sheet)

    @app.post("/campaigns/{campaign_id}/events")
    def append_event(campaign_id: str, payload: EventCreate, repo: Repository = Depends(get_repo)) -> dict:
        return repo.append_event(
            campaign_id=campaign_id,
            actor_type=payload.actor_type,
            actor_id=payload.actor_id,
            visibility=payload.visibility,
            event_type=payload.event_type,
            happened_at=payload.happened_at,
            summary=payload.summary,
            payload=payload.payload,
        )

    @app.post("/campaigns/{campaign_id}/memories")
    def add_memory(campaign_id: str, payload: MemoryCreate, repo: Repository = Depends(get_repo)) -> dict:
        return repo.add_memory(
            text=payload.text,
            scope=payload.scope,
            campaign_id=campaign_id,
            pc_id=payload.pc_id,
            npc_id=payload.npc_id,
            importance=payload.importance,
            visibility=payload.visibility,
            happened_at=payload.happened_at,
            source_event_id=payload.source_event_id,
        )

    @app.get("/campaigns/{campaign_id}/modules")
    def list_modules(campaign_id: str, repo: Repository = Depends(get_repo)) -> list[dict]:
        return repo.list_modules(campaign_id)

    @app.post("/campaigns/{campaign_id}/modules")
    def import_module(campaign_id: str, payload: ModuleImport, repo: Repository = Depends(get_repo)) -> dict:
        chunks = chunk_plaintext_module(
            payload.text,
            title=payload.title,
            visibility=payload.default_visibility,
        )
        return repo.create_module(
            campaign_id=campaign_id,
            title=payload.title,
            chunks=chunks,
            source_type=payload.source_type,
        )

    @app.get("/modules/{module_id}/chunks")
    def list_module_chunks(
        module_id: str,
        view: str = "kp",
        spoiler: str | None = None,
        repo: Repository = Depends(get_repo),
    ) -> list[dict]:
        allowed_visibility = ("player", "table") if view == "player" else ("player", "table", "kp")
        spoiler_tags = tuple(tag.strip() for tag in spoiler.split(",") if tag.strip()) if spoiler else None
        return repo.list_module_chunks(
            module_id,
            allowed_visibility=allowed_visibility,
            spoiler_tags=spoiler_tags,
        )

    @app.get("/campaigns/{campaign_id}/memory/search")
    def search_memory(
        campaign_id: str,
        q: str,
        pc_id: str | None = None,
        repo: Repository = Depends(get_repo),
    ) -> list[dict]:
        results = MemoryRetriever(repo.connection).retrieve(q, campaign_id=campaign_id, pc_id=pc_id)
        return [result.__dict__ for result in results]

    @app.post("/npcs")
    def create_npc(payload: NpcCreate, repo: Repository = Depends(get_repo)) -> dict:
        return repo.create_npc(
            name=payload.name,
            home_location=payload.home_location,
            profession=payload.profession,
            public_notes=payload.public_notes,
            secret_notes=payload.secret_notes,
        )

    @app.post("/campaigns/{campaign_id}/npcs/{npc_id}")
    def link_npc_to_campaign(
        campaign_id: str,
        npc_id: str,
        payload: CampaignNpcLink,
        repo: Repository = Depends(get_repo),
    ) -> dict:
        repo.link_npc_to_campaign(
            campaign_id=campaign_id,
            npc_id=npc_id,
            role=payload.role,
            first_seen_time=payload.first_seen_time,
            last_seen_time=payload.last_seen_time,
            relationship_score=payload.relationship_score,
            notes=payload.notes,
        )
        return {"ok": True}

    @app.get("/campaigns/{campaign_id}/npc-candidates")
    def npc_candidates(
        campaign_id: str,
        action: str,
        location: str | None = None,
        profession_hint: str | None = None,
        repo: Repository = Depends(get_repo),
    ) -> list[dict]:
        results = NpcCandidateService(repo.connection).find_candidates(
            campaign_id=campaign_id,
            action_text=action,
            location=location,
            profession_hint=profession_hint,
        )
        return [result.__dict__ for result in results]

    @app.post("/kp/turn")
    async def kp_turn(payload: KpTurnRequest, settings: Settings = Depends(get_settings)) -> dict:
        connection = connect(settings.db_path)
        init_db(connection)
        try:
            llm = OpenAICompatibleClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
            answer = await KpOrchestrator(connection, llm).handle_player_action(
                campaign_id=payload.campaign_id,
                player_action=payload.player_action,
                pc_id=payload.pc_id,
                location=payload.location,
                profession_hint=payload.profession_hint,
            )
            return {"answer": answer}
        finally:
            connection.close()

    return app


app = create_app()
