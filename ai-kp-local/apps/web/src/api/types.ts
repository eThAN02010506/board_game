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

export type InvestigatorImportPreview = {
  canonical_sheet: CharacterSheet;
  warnings: string[];
  source_hash: string;
  source_filename: string;
  template_id: string;
  parser_version: string;
  ignored_formula_cells: number;
};

export type InvestigatorManualPreview = {
  canonical_sheet: CharacterSheet;
  warnings: string[];
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
  svg_text?: string;
  locations?: MapLocation[];
  routes?: MapRoute[];
  tokens?: MapToken[];
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
  player_action: string;
  public_narration: string;
  kp_notes: string;
  proposed_checks: Record<string, unknown>[];
  proposed_events: Record<string, unknown>[];
  proposed_memories: Record<string, unknown>[];
  proposed_npc_updates: Record<string, unknown>[];
  proposed_map_moves: Record<string, unknown>[];
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
