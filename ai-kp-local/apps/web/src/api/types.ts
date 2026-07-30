export type Role = "kp" | "player";

export type CapabilityStatus = "available" | "partial" | "planned";

export type CapabilityAudience = "all" | "kp" | "player";

export type Capability = {
  id: string;
  label: string;
  status: CapabilityStatus;
  phase: string;
  audience: CapabilityAudience;
  summary: string;
  dependencies: string[];
  acceptance: string[];
};

export type Campaign = {
  id: string;
  title: string;
  system: string;
  current_time: string | null;
};

export type ModuleImportJob = {
  id: string;
  campaign_id: string;
  title: string;
  source_filename: string;
  source_type: "pdf" | "docx";
  source_hash: string;
  status: "queued" | "processing" | "completed" | "failed";
  stage: string;
  progress_current: number;
  progress_total: number;
  attempt_count: number;
  error_text: string | null;
  module_id: string | null;
  created_at: string;
  updated_at: string;
};

export type ModuleRecord = {
  id: string;
  campaign_id: string;
  title: string;
  source_type: string;
  source_filename: string | null;
  source_hash: string | null;
  parser_version: string | null;
  created_at: string;
};

export type ModuleRunStatus = "active" | "paused" | "completed";
export type ModulePlayPace = "freeform" | "structured" | "downtime";
export type ModuleRuntimeEntityStatus = "hidden" | "available" | "discovered" | "resolved";

export type ModuleRun = {
  id: string;
  campaign_id: string;
  module_id: string;
  module_title: string;
  module_source_hash: string | null;
  status: ModuleRunStatus;
  current_scene_key: string | null;
  current_scene_title: string | null;
  play_pace: ModulePlayPace;
  current_location_entity_id: string | null;
  scene_started_world_time: string | null;
  active_spoiler_tags: string[];
  state: Record<string, unknown>;
  version: number;
  started_by_member_id: string | null;
  started_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type ModuleRunEntityState = {
  run_id: string;
  entity_id: string;
  entity_type: ModuleEntity["entity_type"];
  name: string;
  description: string;
  visibility: string;
  spoiler_tag: string | null;
  status: ModuleRuntimeEntityStatus;
  version: number;
  updated_by_member_id: string | null;
  updated_at: string | null;
};

export type ModuleRunSceneEvent = {
  id: string;
  run_id: string;
  from_scene_key: string | null;
  to_scene_key: string;
  from_scene_title: string | null;
  to_scene_title: string;
  from_play_pace: ModulePlayPace | null;
  to_play_pace: ModulePlayPace;
  from_location_entity_id: string | null;
  to_location_entity_id: string | null;
  world_time: string | null;
  note: string;
  created_at: string;
};

export type ModuleRunEntityStateEvent = {
  id: string;
  run_id: string;
  entity_id: string;
  entity_name: string;
  entity_type: ModuleEntity["entity_type"];
  from_status: ModuleRuntimeEntityStatus;
  to_status: ModuleRuntimeEntityStatus;
  note: string;
  created_at: string;
};

export type ModuleRunDirectorState = {
  run: ModuleRun;
  entity_states: ModuleRunEntityState[];
  scene_events: ModuleRunSceneEvent[];
  entity_state_events: ModuleRunEntityStateEvent[];
};

export type SceneTransitionInput = {
  expected_version: number;
  scene_key: string;
  scene_title: string;
  play_pace: ModulePlayPace;
  location_entity_id?: string | null;
  world_time?: string | null;
  note?: string;
};

export type DirectorAnalysis = {
  run_id: string;
  module_id: string;
  module_title: string;
  player_intent: string;
  scene: {
    key: string | null;
    title: string | null;
    play_pace: ModulePlayPace;
    location_entity_id: string | null;
    started_world_time: string | null;
  };
  decision: "needs_scene" | "answer_from_canon" | "blocked_by_spoiler" | "world_gap";
  recommended_action:
    | "pause_for_human_kp"
    | "narrate_existing_world"
    | "propose_world_expansion";
  reasons: string[];
  sources: Array<{
    source_type: string;
    source_id: string;
    title: string;
    text: string;
    source_locator: string | null;
  }>;
  deferred_source_count: number;
  entity_states: ModuleRunEntityState[];
  reachability: ModuleReachabilityReport & { evaluated: boolean };
  unreachable_anchor_count: number;
  writes_performed: false;
};

export type ModuleRunStart = {
  module_id: string;
  current_scene_key?: string | null;
  active_spoiler_tags?: string[];
  state?: Record<string, unknown>;
};

export type ModuleRunUpdate = {
  expected_version: number;
  status?: ModuleRunStatus;
  current_scene_key?: string | null;
  active_spoiler_tags?: string[];
  state?: Record<string, unknown>;
};

export type ModuleChunk = {
  id: string;
  module_id: string;
  title: string;
  text: string;
  visibility: string;
  content_kind: "text" | "table";
  page_start: number | null;
  page_end: number | null;
  paragraph_start: number | null;
  paragraph_end: number | null;
  source_locator: string | null;
  semantic_kind: string;
  classification_confidence: number;
  style_annotations: string[];
  review_flags: string[];
  order_index: number;
  spoiler_tag: string | null;
  knowledge_status: "pending" | "processing" | "completed" | "failed";
};

export type ModuleAsset = {
  id: string;
  module_id: string;
  content_hash: string;
  mime_type: string;
  width: number | null;
  height: number | null;
  source_locator: string;
  nearby_heading: string | null;
  asset_role: string;
  classification_confidence: number;
  review_flags: string[];
  visibility: string;
  spoiler_tag: string | null;
  analysis_status: "pending_analysis" | "completed" | "failed";
  ocr_text: string | null;
  visual_summary: string | null;
  analysis_model: string | null;
  analysis_error: string | null;
};

export type ModuleSearchResult = {
  source_type: "chunk" | "asset" | "knowledge";
  source_id: string;
  module_id: string;
  visibility: string;
  spoiler_tag: string | null;
  source_locator: string;
  title: string;
  text: string;
};

export type ModuleKnowledgeCandidate = {
  id: string;
  module_id: string;
  kind: "module_canon" | "module_anchor" | "reference";
  title: string;
  statement: string;
  rationale: string;
  confidence: number;
  visibility: string;
  spoiler_tag: string | null;
  status: "pending" | "approved" | "rejected";
  review_note: string | null;
  citations: Array<{
    evidence_text: string;
    source_locator: string;
  }>;
};

export type ModuleAnalysisCapabilities = {
  tesseract: {
    available: boolean;
    version: string | null;
    languages: string[];
  };
  vision: {
    configured: boolean;
    model: string;
  };
};

export type ModuleEntity = {
  id: string;
  module_id: string;
  entity_type: "npc" | "location" | "clue" | "organization" | "item" | "event" | "anchor";
  name: string;
  description: string;
  visibility: string;
  spoiler_tag: string | null;
  source_candidate_id: string;
};

export type ModuleEntityRelation = {
  id: string;
  module_id: string;
  source_entity_id: string;
  source_name: string;
  predicate: string;
  target_entity_id: string;
  target_name: string;
  source_candidate_id: string;
  confidence: number;
  note: string;
};

export type ModuleReachabilityReport = {
  module_id: string;
  entry_entity_ids: string[];
  reached_entity_ids: string[];
  anchors: Array<ModuleEntity & { reachable: boolean }>;
  all_anchors_reachable: boolean;
  has_conflicts: boolean;
  safe: boolean;
  conflicts: ModuleEntityRelation[];
};

export type SessionInfo = {
  id: string;
  campaign_id: string;
  title: string;
  status: string;
};

export type SessionMember = {
  id: string;
  session_id: string;
  campaign_id: string;
  role: Role;
  display_name: string;
  pc_id: string | null;
  player_profile_id?: string | null;
  revoked_at: string | null;
};

export type SessionSeat = {
  id: string;
  session_id: string;
  campaign_id: string;
  label: string;
  status: "open" | "claimed" | "revoked";
  assigned_pc_id: string | null;
  player_profile_id: string | null;
  claimed_member_id: string | null;
  profile_display_name: string | null;
  member_display_name: string | null;
  pc_name: string | null;
  has_active_invitation: boolean;
  session_title?: string;
  session_status?: string;
  campaign_title?: string;
};

export type SessionBundle = {
  session: SessionInfo;
  member: SessionMember;
  campaign: Campaign;
  access_token: string;
  join_code?: string;
  seat?: SessionSeat;
  profile?: PlayerProfile;
  player_token?: string;
};

export type AuthIdentity = {
  member_id: string;
  session_id: string;
  campaign_id: string;
  role: Role;
  display_name: string;
  pc_id: string | null;
  player_profile_id?: string | null;
  seat_id?: string | null;
};

export type SkillCheck = {
  id: string;
  campaign_id: string;
  session_id: string;
  proposal_id: string | null;
  player_action_id: string | null;
  requested_by_member_id: string;
  roller_member_id: string | null;
  pc_id: string | null;
  investigator_id: string | null;
  skill_key: string;
  skill_name: string;
  target: number;
  target_source: string;
  difficulty: "regular" | "hard" | "extreme";
  bonus_dice: number;
  hidden: boolean;
  allow_push: boolean;
  pushed_from_check_id: string | null;
  status: "requested" | "resolved" | "overridden" | "cancelled";
  input_method: "digital" | "physical" | null;
  raw_dice: {
    ones_digit: number;
    tens_digits: number[];
    candidates: number[];
  } | null;
  selected_roll: number | null;
  threshold: number | null;
  success_level: "fumble" | "failure" | "regular" | "hard" | "extreme" | "critical" | null;
  passed: boolean | null;
  ruleset_id: string;
  ruleset_version: string;
  source_reference: {
    source_id: string;
    chapter: string;
    page_start: number;
    page_end: number;
    sections: string[];
  };
  investigator_state_version: number | null;
  override_reason: string | null;
  original_result: Record<string, unknown> | null;
  created_at: string;
  resolved_at: string | null;
  actions: Array<{
    id: string;
    action_type: string;
    actor_member_id: string;
    reason: string;
    payload: Record<string, unknown>;
    created_at: string;
  }>;
};

export type CreateSkillCheckInput = {
  skill_name: string;
  difficulty: SkillCheck["difficulty"];
  bonus_dice: number;
  hidden: boolean;
  allow_push: boolean;
  roller_member_id: string | null;
  pc_id: string | null;
  target: number | null;
};

export type PlayerCharacter = {
  id: string;
  campaign_id: string;
  name: string;
  sheet?: Record<string, unknown>;
  public_summary?: {
    occupation?: string;
    cash?: number | string;
    attributes?: Record<string, number>;
    status?: string;
  };
};

export type MemoryClassification = "major" | "side" | "npc" | "clue" | "other";

export type MemoryTimelineItem = {
  id: string;
  campaign_id: string;
  pc_id: string | null;
  pc_name: string | null;
  investigator_id: string | null;
  investigator_name: string | null;
  npc_id: string | null;
  npc_name: string | null;
  scope: string;
  classification: MemoryClassification;
  importance: number;
  visibility: string;
  hidden: boolean;
  text: string;
  happened_at: string | null;
  effective_time: string;
  source_event_id: string | null;
  source_event_type: string | null;
  source_event_summary: string | null;
  source_event_happened_at: string | null;
  source_event_created_at: string | null;
  curation_head_id?: string | null;
  curation_reason?: string | null;
  curated_by_member_id?: string | null;
  curated_at?: string | null;
  created_at: string;
};

export type MemoryCurationInput = {
  classification: MemoryClassification;
  importance: number;
  hidden: boolean;
  reason: string;
  expected_head_action_id: string | null;
};

export type SessionRecapCandidate = {
  id: string;
  run_id: string;
  campaign_id: string;
  order_index: number;
  text: string;
  scope:
    | "campaign_fact"
    | "pc_major"
    | "pc_side"
    | "npc_interaction"
    | "npc_relationship"
    | "location_fact"
    | "clue";
  importance: number;
  visibility: "player" | "table" | "kp";
  pc_id: string | null;
  npc_id: string | null;
  happened_at: string | null;
  source_event_ids: string[];
  rationale: string;
  status: "draft" | "approved" | "rejected";
  review_payload: Record<string, unknown>;
  memory_id: string | null;
  reviewed_by_member_id: string | null;
  reviewed_at: string | null;
  created_at: string;
};

export type SessionRecapRun = {
  id: string;
  campaign_id: string;
  session_id: string;
  status: "draft" | "completed";
  event_window_hash: string;
  event_ids: string[];
  generation_cutoff: string;
  source_model: string;
  prompt_version: string;
  repaired: boolean;
  created_by_member_id: string;
  created_at: string;
  completed_at: string | null;
  candidates: SessionRecapCandidate[];
};

export type SessionRecapReviewInput = {
  action: "approve" | "reject";
  reason: string;
  text?: string;
  scope?: SessionRecapCandidate["scope"];
  importance?: number;
  visibility?: SessionRecapCandidate["visibility"];
  pc_id?: string | null;
  npc_id?: string | null;
  happened_at?: string | null;
};

export type PlayerProfile = {
  id: string;
  display_name: string;
  created_at: string;
  updated_at: string;
};

export type PlayerProfileBundle = {
  profile: PlayerProfile;
  player_token: string;
};

export type CharacterSheet = {
  schema_version: string;
  ruleset_id: string;
  identity: {
    name: string;
    player_name?: string;
    era?: string;
    occupation?: string;
    age?: number;
    gender?: string;
    residence?: string;
    birthplace?: string;
  };
  characteristics: Record<string, number>;
  derived: {
    max_hp: number;
    max_mp: number;
    initial_san: number;
    max_san: number;
    mov: number;
    damage_bonus: string;
    build: number;
    dodge: number;
  };
  skills: Array<{
    skill_key: string;
    display_name: string;
    specialization?: string | null;
    base_value: number;
    occupation_points: number;
    interest_points: number;
    development_points: number;
    current_value: number;
    half_value: number;
    fifth_value: number;
    growth_mark: boolean;
  }>;
  [key: string]: unknown;
};

export type CharacterSkillCatalogItem = {
  skill_key: string;
  display_name: string;
  default_specialization: string | null;
  base_value: number;
  base_formula: "fixed" | "dex_half" | "edu";
  specialization_editable: boolean;
  creation_points_allowed: boolean;
  description: string;
  order: number;
};

export type CharacterSkillRecommendation = {
  profile_id: string;
  profile_name: string;
  occupation_budget: number;
  occupation_spent: number;
  interest_budget: number;
  interest_spent: number;
  allocations: Array<{
    skill_key: string;
    occupation_points: number;
    interest_points: number;
    specialization: string | null;
  }>;
  rationale: string[];
};

export type InvestigatorRevision = {
  id: string;
  investigator_id: string;
  revision_no: number;
  source_type: "manual" | "xlsx";
  canonical_sheet: CharacterSheet;
  public_summary: Record<string, unknown>;
  warnings: string[];
};

export type Investigator = {
  id: string;
  owner_profile_id: string;
  name: string;
  ruleset_id: string;
  current_revision_id: string;
  current_revision: InvestigatorRevision;
};

export type CharacterValidationIssue = {
  layer: "structure" | "ruleset" | "review_policy";
  code: string;
  message: string;
  path: string;
  severity: "warning" | "error";
  requires_kp_review: boolean;
};

export type CharacterValidationReport = {
  issues: CharacterValidationIssue[];
  counts: Record<CharacterValidationIssue["layer"], number>;
  has_errors: boolean;
  requires_kp_review: boolean;
};

export type InvestigatorImportPreview = {
  canonical_sheet: CharacterSheet;
  warnings: string[];
  validation: CharacterValidationReport;
  source_hash: string;
  source_filename: string;
  template_id: string;
  parser_version: string;
  ignored_formula_cells: number;
};

export type InvestigatorManualPreview = {
  canonical_sheet: CharacterSheet;
  warnings: string[];
  validation: CharacterValidationReport;
};

export type CharacterReview = {
  id: string;
  action: "submitted" | "changes_requested" | "approved" | "withdrawn";
  revision_id: string;
  comment: string | null;
  created_at: string;
};

export type InvestigatorCampaignState = {
  campaign_id: string;
  investigator_id: string;
  approved_revision_id: string;
  current_hp: number;
  current_san: number;
  current_mp: number;
  current_luck: number;
  conditions: Array<Record<string, unknown>>;
  inventory_delta: Record<string, unknown>;
  state_version: number;
  current_game_time: string | null;
};

export type CampaignInvestigator = {
  campaign_id: string;
  investigator_id: string;
  owner_profile_id: string;
  name: string;
  status: "draft" | "submitted" | "changes_requested" | "approved" | "withdrawn";
  submitted_revision_id: string | null;
  approved_revision_id: string | null;
  legacy_pc_id: string | null;
  review_comment: string | null;
  submitted_revision: InvestigatorRevision | null;
  approved_revision: InvestigatorRevision | null;
  campaign_state: InvestigatorCampaignState | null;
  reviews: CharacterReview[];
  diff: Array<{ path: string; before: unknown; after: unknown }>;
};

export type RuleSource = {
  id: string;
  ruleset_id: string;
  title: string;
  source_filename: string;
  source_hash: string;
  page_count: number;
  status: "extracting" | "extracted" | "indexing" | "ready" | "failed";
  chunk_count: number;
  rule_count: number;
  object_status_counts?: Record<string, number>;
  latest_run?: {
    stage: string;
    status: string;
    processed_count: number;
    accepted_count: number;
    rejected_count: number;
    cursor_page: number;
  } | null;
};

export type RuleQueryResult = {
  retrieval_backend: "minirag" | "lexical_fallback";
  source: RuleSource;
  chunks: Array<{
    id: string;
    page_start: number;
    page_end: number;
    chapter?: string | null;
    section?: string | null;
    text: string;
  }>;
  rules: Array<{
    id: string;
    rule_key: string;
    title: string;
    status: string;
    object: {
      summary?: string;
      execution?: { kind?: string };
    };
  }>;
};

export type RuleReviewCandidate = {
  id: string;
  source_id: string;
  rule_key: string;
  version: number;
  rule_type: string;
  title: string;
  status: "candidate" | "validated" | "review_required" | "quarantined";
  object_hash: string;
  confidence: number;
  object: {
    [key: string]: unknown;
    summary?: string;
    execution?: { [key: string]: unknown; kind?: string };
    citations?: Array<{
      chunk_id: string;
      page: number;
      evidence_text: string;
      evidence_hash?: string | null;
    }>;
  };
  validation: Record<string, unknown>;
};

export type RuleReviewSubmission = {
  decision: "approved" | "rejected";
  note?: string | null;
  golden_cases: Array<{
    name: string;
    inputs: Record<string, unknown>;
    expected_output: Record<string, unknown>;
  }>;
};

export type PlayerActionRecord = {
  id: string;
  campaign_id: string;
  member_id: string;
  pc_id: string | null;
  action_text: string;
  location: string | null;
  map_id: string | null;
  status: string;
  proposal_id: string | null;
  display_name?: string;
};

export type SavedMap = {
  id: string;
  campaign_id: string;
  title: string;
  prompt: string;
  status: "draft" | "published";
  width: number;
  height: number;
  revision_id?: string;
  revision_no?: number;
  spec_version?: string;
  spec_hash?: string;
  layout_hash?: string;
  svg_text?: string;
  overlay_svg_text?: string;
  map_spec?: MapSpec;
  validation?: MapValidationReport;
  render?: {
    background_asset_url: string | null;
    selected_asset_id: string | null;
    asset_status: "none" | "ready" | "failed";
    fallback_svg: boolean;
  };
  assets?: MapAsset[];
  image_generation?: {
    available: boolean;
    model: string | null;
    safety: "player_safe_projection";
    unavailable_reason?: "provider_not_configured" | "legacy_map_requires_revision" | null;
  };
  locations?: MapLocation[];
  routes?: MapRoute[];
  tokens?: MapToken[];
};

export type MapVisibility = "player" | "table" | "kp";

export type MapSpec = {
  schema_version: "map-spec.v1";
  title: string;
  scene_brief?: string;
  style: string;
  map_kind: "regional" | "site" | "floorplan";
  canvas: {
    width: number;
    height: number;
    coordinate_unit: "logical_px";
    origin: "top_left";
  };
  era: {
    year: number | null;
    locale: string;
    season: string;
    time_of_day: string;
    weather: string;
    public_architecture: string[];
    technology: string[];
    forbidden_visuals?: string[];
  };
  locations: Array<{
    id: string;
    name: string;
    visibility: MapVisibility;
    position: { x: number; y: number };
  }>;
  connections: Array<{
    id: string;
    from_location_id: string;
    to_location_id: string;
    visibility: MapVisibility;
  }>;
  features: Array<{
    id: string;
    name: string;
    location_id: string | null;
    visibility: MapVisibility;
    position: { x: number; y: number };
  }>;
  coverage: {
    required_element_names: string[];
  };
};

export type MapValidationReport = {
  schema_version: "map-spec.v1";
  valid: boolean;
  coverage: {
    required: number;
    covered: number;
    percent: number;
  };
  issues: Array<{
    level: "warning" | "error";
    code: string;
    message: string;
    element_id: string | null;
  }>;
};

export type MapAsset = {
  id: string;
  map_id: string;
  revision_id: string;
  audience: "table" | "kp";
  kind: "background" | "thumbnail";
  status: "ready" | "failed";
  generation_input_hash: string;
  content_hash: string | null;
  mime_type: string | null;
  width: number | null;
  height: number | null;
  provider: string;
  model: string;
  seed: number | null;
  parameters: Record<string, unknown>;
  prompt_text: string;
  error_text: string | null;
  content_url: string;
  cache_hit?: boolean;
  created_at: string;
};

export type MapGenerationInput = {
  title: string;
  prompt: string;
  style: string;
  map_kind: "regional" | "site" | "floorplan";
  locations: string[];
  routes: [string, string][];
  features: string[];
  required_elements: string[];
  era_year: number | null;
  locale: string;
  season: string;
  time_of_day: string;
  weather: string;
  public_architecture: string[];
  forbidden_elements: string[];
  visual_style:
    | "period_illustrated_map"
    | "architectural_blueprint"
    | "ink_atlas"
    | "tactical_floorplan";
  width: number;
  height: number;
};

export type MapLocation = {
  id: string;
  name: string;
  visibility: string;
  x: number;
  y: number;
};

export type MapRoute = {
  id: string;
  start_name: string;
  end_name: string;
  visibility: string;
};

export type MapToken = {
  id: string;
  map_id: string;
  label: string;
  actor_type: string;
  actor_id: string | null;
  location_name: string;
  color: string;
  version: number;
  x: number;
  y: number;
};

export type TurnProposal = {
  id: string;
  campaign_id: string;
  status: string;
  proposal_kind: "standard" | "check_consequence" | "world_expansion";
  check_consequence: {
    proposal_kind: "check_consequence";
    origin_proposal_id: string;
    player_action_id: string;
    check_ids: string[];
    result_fingerprint: string;
  } | null;
  world_expansion: {
    proposal_kind: "world_expansion";
    module_run_id: string;
    module_run_version: number;
    module_id: string;
    module_source_hash: string | null;
    fingerprint: string;
    analysis: {
      fingerprint: string;
      decision: "world_gap";
      reasons: string[];
      scene: DirectorAnalysis["scene"];
      module_id: string;
      module_title: string;
      module_source_hash: string | null;
      module_run_id: string;
      module_run_version: number;
      active_spoiler_tags: string[];
      unreachable_anchor_count: number;
      deferred_source_count: number;
      world_fact_head_hash: string;
      writes_performed: false;
    };
    candidate: {
      expansion_kind: "environment" | "reactive_branch" | "anchor_bridge";
      subject: string;
      proposal: string;
      rationale: string;
      confidence: "low" | "medium" | "high";
      assumptions: string[];
      conflicts: string[];
      alternatives: Array<{
        title: string;
        description: string;
        tradeoff: string;
      }>;
    };
  } | null;
  world_expansion_materialization?: {
    schema_version: "world-expansion-materialization.v1";
    materialization_id: string;
    encounter_event_id: string;
    fact_event_ids: string[];
    npc_id: string | null;
    map_token_id: string | null;
    investigator_encounter_ids?: string[];
    npc_reappearance_id?: string | null;
    created_at: string;
  } | null;
  player_action: string;
  public_narration: string;
  kp_notes: string;
  proposed_checks: Record<string, unknown>[];
  proposed_events: Record<string, unknown>[];
  proposed_memories: Record<string, unknown>[];
  proposed_npc_updates: Record<string, unknown>[];
  proposed_map_moves: Record<string, unknown>[];
};

export type WorldExpansionEncounterInput = {
  idempotency_key: string;
  summary: string;
  happened_at?: string | null;
  facts: Array<{
    fact_type: "canonical_fact" | "kp_secret" | "rumor";
    subject: string;
    predicate: string;
    object_text: string;
  }>;
  npc?: {
    npc_id?: string | null;
    name?: string | null;
    home_location?: string | null;
    profession?: string | null;
    public_notes?: string;
    secret_notes?: string;
    role?: string;
    relationship_score?: number;
    notes?: string;
  } | null;
  map_placement?: {
    map_id: string;
    location_name: string;
    visibility: "table" | "kp";
    color: string;
  } | null;
  participant_investigator_ids?: string[];
  interaction_summary?: string | null;
  profession_context?: string | null;
};

export type NpcReappearanceCandidate = {
  npc_id: string;
  name: string;
  profession: string | null;
  home_location: string | null;
  qualifying_investigators: Array<{
    investigator_id: string;
    investigator_name: string;
    interaction_summary: string;
    happened_at: string | null;
  }>;
  appearance_gate: {
    decision: "eligible" | "needs_review";
    reasons: string[];
    warnings: string[];
    remaining_campaign_budget: number;
    max_returning_npcs: number;
  };
  availability_profile: {
    lifecycle_state: "unknown" | "active" | "missing" | "unavailable";
    born_year: number | null;
    died_year: number | null;
    active_from_year: number | null;
    active_until_year: number | null;
    location_tags: string[];
    profession_tags: string[];
  } | null;
};

export type NpcAvailabilityProfile = {
  npc_id?: string;
  lifecycle_state: "unknown" | "active" | "missing" | "unavailable";
  born_year: number | null;
  died_year: number | null;
  active_from_year: number | null;
  active_until_year: number | null;
  location_tags: string[];
  profession_tags: string[];
  kp_notes?: string;
  updated_at?: string;
};

export type CampaignNpcRecord = {
  id: string;
  name: string;
  home_location: string | null;
  profession: string | null;
  public_notes: string;
  role: string;
  first_seen_time: string | null;
  last_seen_time: string | null;
  relationship_score: number;
  campaign_notes: string;
  availability_profile: NpcAvailabilityProfile | null;
};

export type NpcReappearancePolicy = {
  campaign_id: string;
  max_returning_npcs: number;
  require_location_match: boolean;
  require_profession_match: boolean;
  max_travel_minutes: number;
  updated_at: string | null;
};

export type TravelLocation = {
  id: string;
  campaign_id: string;
  name: string;
  normalized_name: string;
  aliases: string[];
  source_kind: "manual" | "map" | "module";
  source_ref: string | null;
  kp_notes: string;
};

export type TravelRoute = {
  id: string;
  campaign_id: string;
  from_location_id: string;
  to_location_id: string;
  from_name: string;
  to_name: string;
  travel_minutes: number;
  travel_mode: "walk" | "drive" | "rail" | "boat" | "flight" | "other";
  bidirectional: boolean;
  status: "open" | "blocked";
  kp_notes: string;
};

export type TravelGraph = {
  locations: TravelLocation[];
  routes: TravelRoute[];
};

export type TravelRoutePreview = {
  status:
    | "graph_empty"
    | "unresolved_origin"
    | "unresolved_destination"
    | "same_location"
    | "reachable"
    | "over_limit"
    | "unreachable";
  total_minutes: number | null;
  max_minutes: number;
  within_limit: boolean;
  locations: Array<{ id: string; name: string }>;
  legs: Array<{
    route_id: string;
    from_location_id: string;
    to_location_id: string;
    travel_minutes: number;
    travel_mode: string;
  }>;
};

export type HiddenAppearanceDestination = {
  location_name: string;
  weight: number;
};

export type NpcHiddenAppearanceResolution = {
  id: string;
  campaign_id: string;
  npc_id: string;
  npc_name: string;
  idempotency_key: string;
  trigger_text: string;
  appearance_chance: number;
  appearance_roll: number;
  appears: boolean;
  eligible_locations: Array<{
    location_id: string;
    location_name: string;
    requested_name: string;
    weight: number;
    travel_minutes: number;
  }>;
  selected_location_id: string | null;
  selected_location_name: string | null;
  location_roll: number | null;
  created_by_member_id: string;
  created_at: string;
  public_result: {
    appears: boolean;
    location_name: string | null;
  };
};

export type ContextAssembly = {
  proposal_id: string;
  visibility_scope: string;
  token_estimate: number;
  final_prompt: { role: string; content: string }[];
  included_sources: {
    id: string;
    kind: string;
    label: string;
    content: string;
    visibility: string;
    score?: number;
  }[];
  excluded_sources: {
    id: string;
    kind: string;
    label: string;
    excluded_reason?: string;
  }[];
};
