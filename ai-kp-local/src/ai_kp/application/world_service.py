from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from ai_kp.application.ports.repositories import WorldStore
from ai_kp.platform.modules.ingestion import chunk_plaintext_module

WorldView = Literal["player", "kp"]


@dataclass(frozen=True)
class CreatePcCommand:
    name: str
    sheet: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AppendEventCommand:
    actor_type: str
    event_type: str
    summary: str
    actor_id: str | None = None
    visibility: str = "table"
    happened_at: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AddMemoryCommand:
    text: str
    scope: str
    pc_id: str | None = None
    npc_id: str | None = None
    importance: int = 1
    visibility: str = "table"
    happened_at: str | None = None
    source_event_id: str | None = None


@dataclass(frozen=True)
class ImportModuleCommand:
    title: str
    text: str
    source_type: str = "plaintext"
    default_visibility: str = "kp"


@dataclass(frozen=True)
class CreateNpcCommand:
    name: str
    home_location: str | None = None
    profession: str | None = None
    public_notes: str = ""
    secret_notes: str = ""


@dataclass(frozen=True)
class LinkNpcCommand:
    role: str = "encountered"
    first_seen_time: str | None = None
    last_seen_time: str | None = None
    relationship_score: int = 0
    notes: str = ""


class WorldService:
    """Coordinate durable campaign-world writes and visibility-aware queries."""

    def __init__(self, repo: WorldStore):
        self.repo = repo

    def create_pc(
        self,
        campaign_id: str,
        session_id: str,
        command: CreatePcCommand,
    ) -> dict:
        pc = self.repo.create_pc(campaign_id, command.name, command.sheet)
        self.repo.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience="session",
            event_type="pc.created",
            resource_type="player_character",
            resource_id=pc["id"],
        )
        return pc

    def list_pcs(self, campaign_id: str) -> list[dict]:
        return self.repo.list_pcs(campaign_id)

    def append_event(self, campaign_id: str, command: AppendEventCommand) -> dict:
        return self.repo.append_event(
            campaign_id=campaign_id,
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            visibility=command.visibility,
            event_type=command.event_type,
            happened_at=command.happened_at,
            summary=command.summary,
            payload=command.payload,
        )

    def add_memory(self, campaign_id: str, command: AddMemoryCommand) -> dict:
        return self.repo.add_memory(
            text=command.text,
            scope=command.scope,
            campaign_id=campaign_id,
            pc_id=command.pc_id,
            npc_id=command.npc_id,
            importance=command.importance,
            visibility=command.visibility,
            happened_at=command.happened_at,
            source_event_id=command.source_event_id,
        )

    def list_modules(self, campaign_id: str) -> list[dict]:
        return self.repo.list_modules(campaign_id)

    def import_module(self, campaign_id: str, command: ImportModuleCommand) -> dict:
        chunks = chunk_plaintext_module(
            command.text,
            title=command.title,
            visibility=command.default_visibility,
        )
        return self.repo.create_module(
            campaign_id=campaign_id,
            title=command.title,
            chunks=chunks,
            source_type=command.source_type,
        )

    def get_module(self, module_id: str) -> dict:
        """Expose module ownership so the transport can authorize before reading chunks."""

        return self.repo.get_module(module_id)

    def list_module_chunks(
        self,
        module_id: str,
        *,
        view: WorldView,
        spoiler: str | None = None,
    ) -> list[dict]:
        allowed_visibility = (
            (
                "player",
                "table",
            )
            if view == "player"
            else ("player", "table", "kp", "secret")
        )
        if view == "player":
            spoiler_tags: tuple[str, ...] | None = ()
        elif spoiler:
            spoiler_tags = tuple(tag.strip() for tag in spoiler.split(",") if tag.strip())
        else:
            spoiler_tags = None
        return self.repo.list_module_chunks(
            module_id,
            allowed_visibility=allowed_visibility,
            spoiler_tags=spoiler_tags,
        )

    def search_memory(
        self,
        campaign_id: str,
        query: str,
        *,
        pc_id: str | None = None,
        view: WorldView,
    ) -> list[dict]:
        visibility = (
            (
                "player",
                "table",
            )
            if view == "player"
            else ("player", "table", "kp")
        )
        results = self.repo.retrieve_memories(
            query,
            campaign_id=campaign_id,
            pc_id=pc_id,
            visibility=visibility,
        )
        return [asdict(result) for result in results]

    def create_npc(self, command: CreateNpcCommand) -> dict:
        return self.repo.create_npc(
            name=command.name,
            home_location=command.home_location,
            profession=command.profession,
            public_notes=command.public_notes,
            secret_notes=command.secret_notes,
        )

    def create_campaign_npc(
        self,
        campaign_id: str,
        command: CreateNpcCommand,
    ) -> dict:
        npc = self.create_npc(command)
        self.repo.link_npc_to_campaign(campaign_id, npc["id"])
        return npc

    def link_npc(
        self,
        campaign_id: str,
        npc_id: str,
        command: LinkNpcCommand,
    ) -> dict:
        self.repo.link_npc_to_campaign(
            campaign_id=campaign_id,
            npc_id=npc_id,
            role=command.role,
            first_seen_time=command.first_seen_time,
            last_seen_time=command.last_seen_time,
            relationship_score=command.relationship_score,
            notes=command.notes,
        )
        return {"ok": True}

    def npc_candidates(
        self,
        campaign_id: str,
        action_text: str,
        *,
        location: str | None = None,
        profession_hint: str | None = None,
    ) -> list[dict]:
        results = self.repo.find_npc_candidates(
            campaign_id=campaign_id,
            action_text=action_text,
            location=location,
            profession_hint=profession_hint,
        )
        return [asdict(result) for result in results]
