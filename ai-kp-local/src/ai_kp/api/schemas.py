from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_kp.director.turn_output import (
    CheckCandidate,
    EventCandidate,
    MapMoveCandidate,
    MemoryCandidate,
    NpcUpdateCandidate,
)

MAX_CHARACTER_SHEET_BYTES = 512 * 1024
MAX_CHARACTER_JSON_DEPTH = 12
MAX_CHARACTER_JSON_NODES = 10_000
MAX_CHARACTER_CONTAINER_ITEMS = 256
MAX_CHARACTER_STRING_LENGTH = 16_384
CHARACTER_SHEET_FIELDS = {
    "schema_version",
    "ruleset_id",
    "identity",
    "characteristics",
    "derived",
    "skills",
    "combat",
    "assets",
    "background",
    "provenance",
    "extensions",
}


def validate_bounded_character_json(value: dict[str, Any]) -> dict[str, Any]:
    """Reject resource-amplifying or unversioned character-card payloads."""

    import json

    unknown_fields = sorted(set(value) - CHARACTER_SHEET_FIELDS)
    if unknown_fields:
        raise ValueError(
            "Unknown character-sheet fields: " + ", ".join(unknown_fields[:10])
        )

    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_CHARACTER_JSON_NODES:
            raise ValueError("Character sheet contains too many values")
        if depth > MAX_CHARACTER_JSON_DEPTH:
            raise ValueError("Character sheet is nested too deeply")
        if isinstance(item, dict):
            if len(item) > MAX_CHARACTER_CONTAINER_ITEMS:
                raise ValueError("Character sheet object contains too many fields")
            for key, child in item.items():
                if len(str(key)) > 200:
                    raise ValueError("Character sheet field name is too long")
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            if len(item) > MAX_CHARACTER_CONTAINER_ITEMS:
                raise ValueError("Character sheet list contains too many entries")
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str) and len(item) > MAX_CHARACTER_STRING_LENGTH:
            raise ValueError("Character sheet text field is too long")

    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(serialized) > MAX_CHARACTER_SHEET_BYTES:
        raise ValueError("Character sheet exceeds the 512 KiB canonical limit")
    return value


class HealthResponse(BaseModel):
    ok: bool


class CampaignResponse(BaseModel):
    id: str
    title: str
    system: str
    current_time: str | None = None
    created_at: str


class BackupFileResponse(BaseModel):
    archive_path: str
    size: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


class BackupManifestResponse(BaseModel):
    format_version: int
    app_version: str
    schema_version: int
    created_at: str
    files: list[BackupFileResponse]


class BackupSummaryResponse(BaseModel):
    filename: str
    size: int = Field(ge=0)
    created_at: str


class BackupCreateResponse(BaseModel):
    filename: str
    size: int = Field(ge=0)
    manifest: BackupManifestResponse


class BackupVerifyResponse(BaseModel):
    ok: bool
    filename: str
    manifest: BackupManifestResponse


class CampaignCreate(BaseModel):
    title: str
    system: str = "coc7"
    current_time: str | None = None


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    kp_display_name: str = Field(default="KP", min_length=1, max_length=80)


class SessionJoin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    join_code: str = Field(min_length=12, max_length=20)
    display_name: str = Field(min_length=1, max_length=80)


class SessionKpCredentialRecovery(BaseModel):
    kp_display_name: str = Field(min_length=1, max_length=80)


class SessionMemberPcAssign(BaseModel):
    pc_id: str


class SessionSeatCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)


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
    model_config = ConfigDict(extra="forbid")

    canonical_sheet: dict[str, Any]
    source_type: Literal["manual", "xlsx"] = "manual"
    source_hash: str | None = Field(default=None, min_length=64, max_length=64)
    source_filename: str | None = Field(default=None, max_length=255)
    template_id: str | None = Field(default=None, max_length=120)
    parser_version: str | None = Field(default=None, max_length=40)
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("canonical_sheet")
    @classmethod
    def validate_canonical_sheet(
        cls, value: dict[str, Any]
    ) -> dict[str, Any]:
        return validate_bounded_character_json(value)

    @field_validator("warnings")
    @classmethod
    def validate_warnings(cls, value: list[str]) -> list[str]:
        if any(len(warning) > 2000 for warning in value):
            raise ValueError("Investigator warnings cannot exceed 2000 characters")
        return value


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


class ModuleSectionScopeUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    visibility: Literal["player", "table", "kp", "secret"] = "kp"
    spoiler_tag: str | None = Field(default=None, max_length=160)


class ModuleAssetAnalyzeRequest(BaseModel):
    mode: Literal["tesseract", "vision"]
    language: str = Field(default="eng", min_length=2, max_length=80)


class ModuleKnowledgeReview(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_rejection_note(self) -> "ModuleKnowledgeReview":
        if self.decision == "rejected" and not (self.note or "").strip():
            raise ValueError("Rejected candidates require a review note")
        return self


class ModuleRunStart(BaseModel):
    module_id: str = Field(min_length=1, max_length=160)
    current_scene_key: str | None = Field(default=None, max_length=160)
    active_spoiler_tags: list[str] = Field(default_factory=list, max_length=100)
    state: dict[str, Any] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def validate_scope(self) -> "ModuleRunStart":
        if any(not tag.strip() or len(tag.strip()) > 160 for tag in self.active_spoiler_tags):
            raise ValueError("Spoiler tags must contain 1-160 non-whitespace characters")
        return self


class ModuleRunUpdate(BaseModel):
    expected_version: int = Field(ge=0)
    status: Literal["active", "paused", "completed"] | None = None
    current_scene_key: str | None = Field(default=None, max_length=160)
    active_spoiler_tags: list[str] | None = Field(default=None, max_length=100)
    state: dict[str, Any] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_patch(self) -> "ModuleRunUpdate":
        supplied = self.model_fields_set
        if not supplied - {"expected_version"}:
            raise ValueError("At least one module-run field must be supplied")
        for field_name in ("status", "active_spoiler_tags", "state"):
            if field_name in supplied and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        if self.active_spoiler_tags is not None and any(
            not tag.strip() or len(tag.strip()) > 160
            for tag in self.active_spoiler_tags
        ):
            raise ValueError("Spoiler tags must contain 1-160 non-whitespace characters")
        return self


class ModuleSceneTransition(BaseModel):
    expected_version: int = Field(ge=0)
    scene_key: str = Field(min_length=1, max_length=160)
    scene_title: str = Field(min_length=1, max_length=300)
    play_pace: Literal["freeform", "structured", "downtime"] = "freeform"
    location_entity_id: str | None = Field(default=None, max_length=160)
    world_time: str | None = Field(default=None, max_length=160)
    note: str = Field(default="", max_length=2000)


class ModuleRunEntityStateUpdate(BaseModel):
    expected_version: int = Field(ge=0)
    status: Literal["hidden", "available", "discovered", "resolved"]
    note: str = Field(default="", max_length=2000)


class DirectorAnalysisRequest(BaseModel):
    player_intent: str = Field(min_length=1, max_length=1000)


class WorldExpansionProposalRequest(BaseModel):
    player_intent: str = Field(min_length=1, max_length=1000)
    pc_id: str | None = Field(default=None, max_length=160)
    map_id: str | None = Field(default=None, max_length=160)


class ModuleReachabilityCheck(BaseModel):
    entry_entity_ids: list[str] = Field(min_length=1, max_length=100)


class InvestigatorSubmit(BaseModel):
    revision_id: str | None = None
    timeline_branch_id: str | None = Field(default=None, max_length=160)


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


class InvestigatorTimelineBranchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)


class InvestigatorPermanentChangeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "major_experience",
        "scar",
        "relationship",
        "spell",
        "characteristic",
        "skill",
    ]
    summary: str = Field(min_length=1, max_length=1000)
    change: dict[str, Any] = Field(default_factory=dict, max_length=4)
    source_event_id: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=2000)


class InvestigatorPermanentChangeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["accepted", "rejected"]
    reason: str = Field(min_length=1, max_length=1000)
    expected_revision_id: str = Field(min_length=1, max_length=160)


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
    model_config = ConfigDict(extra="forbid")

    role: str = "encountered"
    first_seen_time: str | None = None
    last_seen_time: str | None = None
    relationship_score: int = 0
    notes: str = ""
    participant_investigator_ids: list[str] = Field(
        default_factory=list,
        max_length=12,
    )


class NpcAvailabilityProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lifecycle_state: Literal["unknown", "active", "missing", "unavailable"] = "unknown"
    born_year: int | None = Field(default=None, ge=1, le=9999)
    died_year: int | None = Field(default=None, ge=1, le=9999)
    active_from_year: int | None = Field(default=None, ge=1, le=9999)
    active_until_year: int | None = Field(default=None, ge=1, le=9999)
    location_tags: list[str] = Field(default_factory=list, max_length=30)
    profession_tags: list[str] = Field(default_factory=list, max_length=30)
    kp_notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_ranges(self) -> "NpcAvailabilityProfileUpdate":
        if self.born_year and self.died_year and self.died_year < self.born_year:
            raise ValueError("died_year cannot be before born_year")
        if (
            self.active_from_year
            and self.active_until_year
            and self.active_until_year < self.active_from_year
        ):
            raise ValueError("active_until_year cannot be before active_from_year")
        for values in (self.location_tags, self.profession_tags):
            normalized = [" ".join(item.split()) for item in values]
            if any(not item or len(item) > 120 for item in normalized):
                raise ValueError("availability tags must contain 1-120 characters")
            if len({item.casefold() for item in normalized}) != len(normalized):
                raise ValueError("availability tags must be unique")
        return self


class CampaignNpcReappearancePolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_returning_npcs: int = Field(default=1, ge=0, le=50)
    require_location_match: bool = False
    require_profession_match: bool = False
    max_travel_minutes: int = Field(default=1440, ge=0, le=525_600)


class TravelLocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    source_kind: Literal["manual", "map", "module"] = "manual"
    source_ref: str | None = Field(default=None, max_length=300)
    kp_notes: str = Field(default="", max_length=2000)


class TravelRouteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_location_id: str = Field(min_length=1, max_length=100)
    to_location_id: str = Field(min_length=1, max_length=100)
    travel_minutes: int = Field(ge=1, le=525_600)
    travel_mode: Literal["walk", "drive", "rail", "boat", "flight", "other"] = "other"
    bidirectional: bool = True
    status: Literal["open", "blocked"] = "open"
    kp_notes: str = Field(default="", max_length=2000)


class TravelRoutePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origins: list[str] = Field(min_length=1, max_length=30)
    destination: str = Field(min_length=1, max_length=200)
    max_minutes: int = Field(ge=0, le=525_600)


class HiddenAppearanceDestinationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_name: str = Field(min_length=1, max_length=200)
    weight: int = Field(default=1, ge=1, le=1000)


class HiddenAppearanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=200)
    npc_id: str = Field(min_length=1, max_length=100)
    trigger_text: str = Field(min_length=1, max_length=500)
    appearance_chance: int = Field(ge=0, le=100)
    destinations: list[HiddenAppearanceDestinationInput] = Field(
        min_length=1,
        max_length=20,
    )
    profession_hint: str | None = Field(default=None, max_length=200)


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


class MemoryCurationCreate(BaseModel):
    classification: Literal["major", "side", "npc", "clue", "other"]
    importance: int = Field(ge=1, le=5)
    hidden: bool = False
    reason: str = Field(min_length=1, max_length=1000)
    expected_head_action_id: str | None = None


class SessionRecapReview(BaseModel):
    action: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=1000)
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    scope: Literal[
        "campaign_fact",
        "pc_major",
        "pc_side",
        "npc_interaction",
        "npc_relationship",
        "location_fact",
        "clue",
    ] | None = None
    importance: int | None = Field(default=None, ge=1, le=5)
    visibility: Literal["player", "table", "kp"] | None = None
    pc_id: str | None = None
    npc_id: str | None = None
    happened_at: str | None = None


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
