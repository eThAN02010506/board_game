from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_kp.director.turn_output import (
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


class SessionKpCredentialRecovery(BaseModel):
    kp_display_name: str = Field(min_length=1, max_length=80)


class SessionMemberPcAssign(BaseModel):
    pc_id: str


class SessionSeatCreate(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    pc_id: str | None = None


class SessionSeatClaim(BaseModel):
    invitation_code: str = Field(min_length=12, max_length=20)
    display_name: str = Field(min_length=1, max_length=80)


class SessionSeatPcAssign(BaseModel):
    pc_id: str | None = None


class SkillCheckCreate(BaseModel):
    skill_name: str = Field(min_length=1, max_length=100)
    difficulty: Literal["regular", "hard", "extreme"] = "regular"
    bonus_dice: int = Field(default=0, ge=-2, le=2)
    hidden: bool = False
    allow_push: bool = True
    roller_member_id: str | None = None
    pc_id: str | None = None
    target: int | None = Field(default=None, ge=0, le=100)
    proposal_id: str | None = None
    player_action_id: str | None = None


class SkillCheckResolve(BaseModel):
    input_method: Literal["digital", "physical"] = "digital"
    ones_digit: int | None = Field(default=None, ge=0, le=9)
    tens_digits: list[int] = Field(default_factory=list, max_length=3)


class SkillCheckOverride(BaseModel):
    success_level: Literal[
        "fumble", "failure", "regular", "hard", "extreme", "critical"
    ]
    passed: bool
    reason: str = Field(min_length=1, max_length=1000)


class SkillCheckDecision(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


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


class InvestigatorSkillRecommendationRequest(BaseModel):
    occupation: str = Field(min_length=1, max_length=120)
    era: str = Field(min_length=1, max_length=80)
    age: int = Field(ge=15, le=89)
    occupation_point_formula: Literal[
        "edu4",
        "edu2_app2",
        "edu2_dex2",
        "edu2_pow2",
        "edu2_str2",
    ] = "edu4"
    characteristics: dict[str, int] = Field(default_factory=dict)


class ModelConfigurationUpdate(BaseModel):
    provider_type: Literal["openai_compatible", "local_mlx"]
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    model: str = Field(default="", max_length=300)
    local_model_path: str | None = Field(default=None, max_length=2000)
    local_port: int = Field(default=8011, ge=1024, le=65535)


class ImageModelConfigurationUpdate(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    model: str = Field(default="", max_length=300)
    timeout_seconds: float = Field(default=300, ge=10, le=1800)


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
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=2, max_length=4000)
    style: str = Field(default="investigation", min_length=1, max_length=80)
    map_kind: Literal["regional", "site", "floorplan"] = "regional"
    locations: list[str] = Field(default_factory=list, max_length=32)
    routes: list[tuple[str, str]] = Field(default_factory=list, max_length=64)
    features: list[str] = Field(default_factory=list, max_length=48)
    required_elements: list[str] = Field(default_factory=list, max_length=80)
    era_year: int | None = Field(default=None, ge=1000, le=2100)
    locale: str = Field(default="", max_length=160)
    season: str = Field(default="", max_length=80)
    time_of_day: str = Field(default="", max_length=80)
    weather: str = Field(default="", max_length=120)
    public_architecture: list[str] = Field(default_factory=list, max_length=20)
    forbidden_elements: list[str] = Field(default_factory=list, max_length=40)
    visual_style: Literal[
        "period_illustrated_map",
        "architectural_blueprint",
        "ink_atlas",
        "tactical_floorplan",
    ] = "period_illustrated_map"
    width: int = Field(default=960, ge=640, le=2048)
    height: int = Field(default=640, ge=480, le=2048)

    @model_validator(mode="after")
    def validate_structure(self) -> "MapGenerateRequest":
        self.title = self.title.strip()
        self.prompt = self.prompt.strip()
        self.locations = [item.strip() for item in self.locations if item.strip()]
        self.routes = [
            (start.strip(), end.strip())
            for start, end in self.routes
            if start.strip() and end.strip()
        ]
        self.features = [item.strip() for item in self.features if item.strip()]
        self.required_elements = [
            item.strip() for item in self.required_elements if item.strip()
        ]
        self.public_architecture = [
            item.strip() for item in self.public_architecture if item.strip()
        ]
        self.forbidden_elements = [
            item.strip() for item in self.forbidden_elements if item.strip()
        ]
        if len(self.locations) != len(set(self.locations)):
            raise ValueError("地点名称不能重复")
        if len(self.features) != len(set(self.features)):
            raise ValueError("场景元素不能重复")
        if any(len(item) > 100 for item in (*self.locations, *self.features)):
            raise ValueError("地点和场景元素名称不能超过 100 个字符")
        if self.locations:
            known = set(self.locations)
            unknown = sorted(
                {
                    endpoint
                    for route in self.routes
                    for endpoint in route
                    if endpoint not in known
                }
            )
            if unknown:
                raise ValueError(f"路线引用了未知地点：{', '.join(unknown)}")
        available = set(self.locations) | set(self.features)
        missing = sorted(set(self.required_elements) - available)
        if missing:
            raise ValueError(f"必需元素尚未列入地点或场景元素：{', '.join(missing)}")
        return self


class MapImageGenerateRequest(BaseModel):
    width: int = Field(default=1024, ge=512, le=2048)
    height: int = Field(default=1024, ge=512, le=2048)
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)


class MapPublishRequest(BaseModel):
    expected_revision_id: str = Field(min_length=1, max_length=100)
    expected_selected_asset_id: str | None = Field(default=None, max_length=100)


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
