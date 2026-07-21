from typing import Any, Literal

from pydantic import BaseModel, Field

from ai_kp.kp.turn_output import (
    CheckCandidate,
    EventCandidate,
    MapMoveCandidate,
    MemoryCandidate,
    NpcUpdateCandidate,
)


class CampaignCreate(BaseModel):
    title: str
    system: str = "coc7"
    current_time: str | None = None


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    kp_display_name: str = Field(default="KP", min_length=1, max_length=80)


class SessionJoin(BaseModel):
    join_code: str = Field(min_length=12, max_length=20)
    display_name: str = Field(min_length=1, max_length=80)
    pc_id: str | None = None


class SessionMemberPcAssign(BaseModel):
    pc_id: str


class PlayerActionCreate(BaseModel):
    action_text: str = Field(min_length=1, max_length=4000)
    token_id: str | None = None
    map_id: str | None = None
    client_action_id: str | None = Field(default=None, min_length=8, max_length=100)


class PcCreate(BaseModel):
    name: str
    sheet: dict = Field(default_factory=dict)


class PlayerProfileCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


class InvestigatorCreate(BaseModel):
    canonical_sheet: dict[str, Any]
    source_type: Literal["manual", "xlsx"] = "manual"
    source_hash: str | None = None
    source_filename: str | None = Field(default=None, max_length=255)
    template_id: str | None = Field(default=None, max_length=120)
    parser_version: str | None = Field(default=None, max_length=40)
    warnings: list[str] = Field(default_factory=list, max_length=100)


class InvestigatorSubmit(BaseModel):
    revision_id: str | None = None


class InvestigatorReview(BaseModel):
    action: Literal["approved", "changes_requested"]
    comment: str | None = Field(default=None, max_length=2000)


class InvestigatorAssignment(BaseModel):
    investigator_id: str


class InvestigatorCampaignStateUpdate(BaseModel):
    expected_version: int = Field(ge=0)
    current_hp: int | None = None
    current_san: int | None = None
    current_mp: int | None = None
    current_luck: int | None = None
    conditions: list[dict[str, Any]] | None = None
    inventory_delta: dict[str, Any] | None = None
    current_game_time: str | None = Field(default=None, max_length=120)


class RuleQueryRequest(BaseModel):
    ruleset_id: str = Field(default="coc7-keeper-cn-2002c", max_length=120)
    question: str = Field(min_length=2, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=30)


class RuleExecuteRequest(BaseModel):
    ruleset_id: str = Field(default="coc7-keeper-cn-2002c", max_length=120)
    rule_key: str = Field(min_length=3, max_length=160)
    inputs: dict[str, Any] = Field(default_factory=dict)


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


class MapGenerateRequest(BaseModel):
    title: str
    prompt: str
    style: str = "investigation"
    locations: list[str] = Field(default_factory=list)
    routes: list[tuple[str, str]] = Field(default_factory=list)
    width: int = 960
    height: int = 640


class MapTokenCreate(BaseModel):
    label: str
    location_name: str
    actor_type: str = "pc"
    actor_id: str | None = None
    visibility: str = "table"
    color: str = "#b93f2d"


class MapTokenMove(BaseModel):
    to_location_name: str
    note: str = ""
    require_route: bool = True
    expected_version: int | None = Field(default=None, ge=0)


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
    player_action: str = Field(min_length=1, max_length=4000)
    player_action_id: str | None = None
    pc_id: str | None = None
    location: str | None = None
    map_id: str | None = None
    profession_hint: str | None = None
    active_spoiler_tags: list[str] = Field(default_factory=list)


class ProposalDecision(BaseModel):
    note: str = ""
    override_public_narration: str | None = None


class TurnProposalCreate(BaseModel):
    player_action: str = Field(min_length=1, max_length=4000)
    player_action_id: str | None = None
    public_narration: str = Field(min_length=1, max_length=12000)
    pc_id: str | None = None
    kp_notes: str = ""
    proposed_checks: list[CheckCandidate] = Field(default_factory=list, max_length=8)
    proposed_events: list[EventCandidate] = Field(default_factory=list, max_length=12)
    proposed_memories: list[MemoryCandidate] = Field(default_factory=list, max_length=10)
    proposed_npc_updates: list[NpcUpdateCandidate] = Field(default_factory=list, max_length=8)
    proposed_map_moves: list[MapMoveCandidate] = Field(default_factory=list, max_length=12)
    source_model: str = "manual-dev"
