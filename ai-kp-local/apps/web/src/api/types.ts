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
  revoked_at: string | null;
};

export type SessionBundle = {
  session: SessionInfo;
  member: SessionMember;
  campaign: Campaign;
  access_token: string;
  join_code?: string;
};

export type AuthIdentity = {
  member_id: string;
  session_id: string;
  campaign_id: string;
  role: Role;
  display_name: string;
  pc_id: string | null;
};

export type PlayerCharacter = {
  id: string;
  campaign_id: string;
  name: string;
  sheet?: Record<string, unknown>;
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
