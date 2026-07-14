from pydantic import BaseModel, Field


class CampaignCreate(BaseModel):
    title: str
    system: str = "coc7"
    current_time: str | None = None


class PcCreate(BaseModel):
    name: str
    sheet: dict = Field(default_factory=dict)


class NpcCreate(BaseModel):
    name: str
    home_location: str | None = None
    profession: str | None = None
    public_notes: str = ""
    secret_notes: str = ""


class CampaignNpcLink(BaseModel):
    role: str = "encountered"
    first_seen_time: str | None = None
    last_seen_time: str | None = None
    relationship_score: int = 0
    notes: str = ""


class ModuleImport(BaseModel):
    title: str
    text: str
    source_type: str = "plaintext"
    default_visibility: str = "kp"


class EventCreate(BaseModel):
    actor_type: str
    event_type: str
    summary: str
    actor_id: str | None = None
    visibility: str = "table"
    happened_at: str | None = None
    payload: dict = Field(default_factory=dict)


class MemoryCreate(BaseModel):
    text: str
    scope: str
    pc_id: str | None = None
    npc_id: str | None = None
    importance: int = 1
    visibility: str = "table"
    happened_at: str | None = None
    source_event_id: str | None = None


class KpTurnRequest(BaseModel):
    campaign_id: str
    player_action: str
    pc_id: str | None = None
    location: str | None = None
    profession_hint: str | None = None
