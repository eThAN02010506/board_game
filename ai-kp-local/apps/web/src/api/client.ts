import type {
  Capability,
  ModuleRun,
  ModuleRunDirectorState,
  ModuleRuntimeEntityStatus,
  SceneTransitionInput,
  DirectorAnalysis,
  DynamicBranchRun,
  ModuleRunStart,
  ModuleRunUpdate,
  NpcReappearanceCandidate,
  CampaignNpcRecord,
  NpcAvailabilityProfile,
  NpcReappearancePolicy,
  HiddenAppearanceDestination,
  MemoryClassification,
  MemoryCurationInput,
  MemoryTimelineItem,
  SessionRecapCandidate,
  SessionRecapReviewInput,
  SessionRecapRun,
  NpcHiddenAppearanceResolution,
  TravelGraph,
  TravelLocation,
  TravelRoute,
  TravelRoutePreview,
  RuleReviewCandidate,
  RuleReviewSubmission,
  Role,
  TurnProposal,
  WorldExpansionEncounterInput,
  WorldFactCreateInput,
  WorldFactEntry,
  WorldFactType
} from "./types";

export const apiBase = "/api";
const defaultRequestTimeoutMs = 310_000;

type CredentialSnapshot = {
  accessToken: string;
  adminToken: string;
  role: Role | "";
  pcId: string | null;
  playerToken: string;
};

const credentials: CredentialSnapshot = {
  accessToken: "",
  adminToken: "",
  role: "",
  pcId: null,
  playerToken: ""
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export function isApiError(error: unknown, status?: number): error is ApiError {
  return error instanceof ApiError && (status === undefined || error.status === status);
}

async function responseError(response: Response): Promise<ApiError> {
  const text = await response.text();
  if (!text) {
    return new ApiError(
      `${response.status} ${response.statusText}`.trim(),
      response.status
    );
  }
  try {
    const payload = JSON.parse(text) as { detail?: unknown; code?: unknown };
    const code = typeof payload.code === "string" ? payload.code : null;
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return new ApiError(payload.detail, response.status, code);
    }
    if (payload.detail !== undefined) {
      return new ApiError(JSON.stringify(payload.detail), response.status, code);
    }
  } catch {
    // Non-JSON error bodies are already suitable for display.
  }
  return new ApiError(text, response.status);
}

async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs = defaultRequestTimeoutMs
): Promise<Response> {
  const controller = new AbortController();
  const upstreamSignal = init.signal;
  const abortFromUpstream = () => controller.abort(upstreamSignal?.reason);
  if (upstreamSignal?.aborted) abortFromUpstream();
  else upstreamSignal?.addEventListener("abort", abortFromUpstream, { once: true });
  const timeout = window.setTimeout(
    () => controller.abort(new DOMException("请求超时", "TimeoutError")),
    timeoutMs
  );
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (error) {
    if (controller.signal.reason instanceof DOMException &&
        controller.signal.reason.name === "TimeoutError") {
      throw new Error("请求超时，请检查后端或模型服务是否仍在运行");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
    upstreamSignal?.removeEventListener("abort", abortFromUpstream);
  }
}

export const credentialBridge = {
  session(accessToken: string, role: Role | "" = "", pcId: string | null = null) {
    credentials.accessToken = accessToken;
    credentials.role = role;
    credentials.pcId = pcId;
  },
  admin(adminToken: string) {
    credentials.adminToken = adminToken;
  },
  player(playerToken: string) {
    credentials.playerToken = playerToken;
  },
  snapshot(): Readonly<CredentialSnapshot> {
    return { ...credentials };
  }
};

async function sendJson<T>(
  url: string,
  init: RequestInit | undefined,
  accessToken: string,
  adminToken: string
): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }
  if (adminToken) {
    headers.set("X-AI-KP-Admin-Token", adminToken);
  }
  if (credentials.playerToken) {
    headers.set("X-AI-KP-Player-Token", credentials.playerToken);
  }
  const response = await fetchWithTimeout(`${apiBase}${url}`, { ...init, headers });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json() as Promise<T>;
}

export function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  return sendJson(url, init, credentials.accessToken, credentials.adminToken);
}

export function requestJsonWithAccessToken<T>(
  url: string,
  accessToken: string,
  init?: RequestInit
): Promise<T> {
  return sendJson(url, init, accessToken, credentials.adminToken);
}

export function listMemoryTimeline(
  campaignId: string,
  options: {
    pcId?: string;
    classification?: MemoryClassification;
    query?: string;
    includeHidden?: boolean;
  } = {}
): Promise<MemoryTimelineItem[]> {
  const params = new URLSearchParams();
  if (options.pcId) params.set("pc_id", options.pcId);
  if (options.classification) params.set("classification", options.classification);
  if (options.query?.trim()) params.set("q", options.query.trim());
  if (options.includeHidden) params.set("include_hidden", "true");
  const query = params.size ? `?${params.toString()}` : "";
  return requestJson<MemoryTimelineItem[]>(
    `/campaigns/${encodeURIComponent(campaignId)}/memory/timeline${query}`
  );
}

export function curateMemory(
  campaignId: string,
  memoryId: string,
  payload: MemoryCurationInput
): Promise<Record<string, unknown>> {
  return requestJson(
    `/campaigns/${encodeURIComponent(campaignId)}/memories/${encodeURIComponent(memoryId)}/curation`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function generateSessionRecap(sessionId: string): Promise<SessionRecapRun> {
  return requestJson<SessionRecapRun>(
    `/sessions/${encodeURIComponent(sessionId)}/recaps/generate`,
    { method: "POST" }
  );
}

export function getLatestSessionRecap(
  sessionId: string
): Promise<SessionRecapRun | null> {
  return requestJson<SessionRecapRun | null>(
    `/sessions/${encodeURIComponent(sessionId)}/recaps/latest`
  );
}

export function reviewSessionRecapCandidate(
  candidateId: string,
  payload: SessionRecapReviewInput
): Promise<SessionRecapCandidate> {
  return requestJson<SessionRecapCandidate>(
    `/session-recap-candidates/${encodeURIComponent(candidateId)}/review`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export async function requestBlob(url: string, signal?: AbortSignal): Promise<Blob> {
  const headers = new Headers();
  if (credentials.accessToken) {
    headers.set("Authorization", `Bearer ${credentials.accessToken}`);
  }
  if (credentials.adminToken) {
    headers.set("X-AI-KP-Admin-Token", credentials.adminToken);
  }
  const response = await fetchWithTimeout(`${apiBase}${url}`, { headers, signal });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.blob();
}

export function fetchCapabilities(): Promise<Capability[]> {
  return requestJson<Capability[]>("/capabilities");
}

export function listWorldFacts(
  campaignId: string,
  options: {
    includeHistory?: boolean;
    factType?: WorldFactType | "";
  } = {}
): Promise<WorldFactEntry[]> {
  const params = new URLSearchParams();
  if (options.includeHistory) params.set("include_history", "true");
  if (options.factType) params.set("fact_type", options.factType);
  const query = params.size ? `?${params.toString()}` : "";
  return requestJson<WorldFactEntry[]>(
    `/campaigns/${encodeURIComponent(campaignId)}/facts${query}`
  );
}

export function createWorldFact(
  campaignId: string,
  payload: WorldFactCreateInput
): Promise<WorldFactEntry> {
  return requestJson<WorldFactEntry>(
    `/campaigns/${encodeURIComponent(campaignId)}/facts`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function retconWorldFact(
  campaignId: string,
  factKey: string,
  payload: {
    expected_head_event_id: string;
    reason: string;
    evidence_event_ids?: string[];
    source_reference?: Record<string, unknown>;
    happened_at?: string | null;
  }
): Promise<{
  append_only: true;
  retained_revision: WorldFactEntry;
  appended_revision: WorldFactEntry;
}> {
  return requestJson(
    `/campaigns/${encodeURIComponent(campaignId)}/facts/${encodeURIComponent(factKey)}/retcon`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function listModuleRuns(
  campaignId: string,
  options: { limit?: number; offset?: number } = {}
): Promise<ModuleRun[]> {
  const params = new URLSearchParams();
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.offset !== undefined) params.set("offset", String(options.offset));
  const query = params.size ? `?${params.toString()}` : "";
  return requestJson<ModuleRun[]>(
    `/campaigns/${encodeURIComponent(campaignId)}/module-runs${query}`
  );
}

export function getCurrentModuleRun(campaignId: string): Promise<ModuleRun | null> {
  return requestJson<ModuleRun | null>(
    `/campaigns/${encodeURIComponent(campaignId)}/module-runs/current`
  );
}

export function startModuleRun(
  campaignId: string,
  payload: ModuleRunStart
): Promise<ModuleRun> {
  return requestJson<ModuleRun>(
    `/campaigns/${encodeURIComponent(campaignId)}/module-runs`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function updateModuleRun(
  runId: string,
  payload: ModuleRunUpdate
): Promise<ModuleRun> {
  return requestJson<ModuleRun>(
    `/module-runs/${encodeURIComponent(runId)}`,
    { method: "PATCH", body: JSON.stringify(payload) }
  );
}

export function getModuleRunDirectorState(
  runId: string
): Promise<ModuleRunDirectorState> {
  return requestJson<ModuleRunDirectorState>(
    `/module-runs/${encodeURIComponent(runId)}/director-state`
  );
}

export function transitionModuleRunScene(
  runId: string,
  payload: SceneTransitionInput
): Promise<{ run: ModuleRun; event: ModuleRunDirectorState["scene_events"][number] }> {
  return requestJson(
    `/module-runs/${encodeURIComponent(runId)}/scene-transitions`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function updateModuleRunEntityState(
  runId: string,
  entityId: string,
  payload: {
    expected_version: number;
    status: ModuleRuntimeEntityStatus;
    note?: string;
  }
): Promise<{
  run: ModuleRun;
  entity_state: ModuleRunDirectorState["entity_states"][number];
  event: ModuleRunDirectorState["entity_state_events"][number] | null;
}> {
  return requestJson(
    `/module-runs/${encodeURIComponent(runId)}/entities/${
      encodeURIComponent(entityId)
    }/state`,
    { method: "PATCH", body: JSON.stringify(payload) }
  );
}

export function analyzeModuleRunIntent(
  runId: string,
  playerIntent: string
): Promise<DirectorAnalysis> {
  return requestJson<DirectorAnalysis>(
    `/module-runs/${encodeURIComponent(runId)}/director/analyze`,
    {
      method: "POST",
      body: JSON.stringify({ player_intent: playerIntent })
    }
  );
}

export function updateDirectorControl(
  runId: string,
  payload: {
    expected_version: number;
    mode: ModuleRun["director_control_mode"];
    reason: string;
  }
): Promise<ModuleRun> {
  return requestJson<ModuleRun>(
    `/module-runs/${encodeURIComponent(runId)}/director/control`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function generateWorldExpansionProposal(
  runId: string,
  payload: {
    player_intent: string;
    pc_id?: string | null;
    map_id?: string | null;
  }
): Promise<TurnProposal> {
  return requestJson<TurnProposal>(
    `/module-runs/${encodeURIComponent(runId)}/director/world-expansion-proposals`,
    {
      method: "POST",
      body: JSON.stringify(payload)
    }
  );
}

export function materializeWorldExpansionEncounter(
  proposalId: string,
  payload: WorldExpansionEncounterInput
): Promise<TurnProposal> {
  return requestJson<TurnProposal>(
    `/kp/proposals/${encodeURIComponent(proposalId)}/world-expansion-encounters`,
    {
      method: "POST",
      body: JSON.stringify(payload)
    }
  );
}

export function listDynamicBranches(
  campaignId: string,
  status?: DynamicBranchRun["status"]
): Promise<DynamicBranchRun[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return requestJson<DynamicBranchRun[]>(
    `/campaigns/${encodeURIComponent(campaignId)}/dynamic-branches${query}`
  );
}

export function resolveDynamicBranchBeat(
  branchId: string,
  payload: {
    expected_version: number;
    command_id: string;
    outcome: "succeeded" | "failed" | "skipped";
    note: string;
    observed_effects: string[];
  }
): Promise<{
  branch: DynamicBranchRun;
  event: DynamicBranchRun["events"][number];
  idempotent_replay: boolean;
}> {
  return requestJson(
    `/dynamic-branches/${encodeURIComponent(branchId)}/beats/resolve`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function resumeDynamicBranch(
  branchId: string,
  payload: { expected_version: number; command_id: string; note: string }
): Promise<{
  branch: DynamicBranchRun;
  event: DynamicBranchRun["events"][number];
  idempotent_replay: boolean;
}> {
  return requestJson(
    `/dynamic-branches/${encodeURIComponent(branchId)}/resume`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function abandonDynamicBranch(
  branchId: string,
  payload: { expected_version: number; command_id: string; note: string }
): Promise<{
  branch: DynamicBranchRun;
  event: DynamicBranchRun["events"][number];
  idempotent_replay: boolean;
}> {
  return requestJson(
    `/dynamic-branches/${encodeURIComponent(branchId)}/abandon`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function listNpcReappearanceCandidates(
  campaignId: string,
  query = "",
  context: { location?: string; professionHint?: string } = {}
): Promise<NpcReappearanceCandidate[]> {
  const params = new URLSearchParams();
  if (query.trim()) params.set("query", query.trim());
  if (context.location?.trim()) {
    params.set("context_location", context.location.trim());
  }
  if (context.professionHint?.trim()) {
    params.set("profession_hint", context.professionHint.trim());
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  return requestJson<NpcReappearanceCandidate[]>(
    `/campaigns/${encodeURIComponent(campaignId)}/npc-reappearance-candidates${suffix}`
  );
}

export function listCampaignNpcs(campaignId: string): Promise<CampaignNpcRecord[]> {
  return requestJson<CampaignNpcRecord[]>(
    `/campaigns/${encodeURIComponent(campaignId)}/npcs`
  );
}

export function saveNpcAvailability(
  campaignId: string,
  npcId: string,
  payload: Omit<NpcAvailabilityProfile, "npc_id" | "updated_at">
): Promise<NpcAvailabilityProfile> {
  return requestJson<NpcAvailabilityProfile>(
    `/campaigns/${encodeURIComponent(campaignId)}/npcs/${encodeURIComponent(npcId)}/availability`,
    { method: "PUT", body: JSON.stringify(payload) }
  );
}

export function getNpcReappearancePolicy(
  campaignId: string
): Promise<NpcReappearancePolicy> {
  return requestJson<NpcReappearancePolicy>(
    `/campaigns/${encodeURIComponent(campaignId)}/npc-reappearance-policy`
  );
}

export function saveNpcReappearancePolicy(
  campaignId: string,
  payload: Omit<NpcReappearancePolicy, "campaign_id" | "updated_at">
): Promise<NpcReappearancePolicy> {
  return requestJson<NpcReappearancePolicy>(
    `/campaigns/${encodeURIComponent(campaignId)}/npc-reappearance-policy`,
    { method: "PUT", body: JSON.stringify(payload) }
  );
}

export function getTravelGraph(campaignId: string): Promise<TravelGraph> {
  return requestJson<TravelGraph>(
    `/campaigns/${encodeURIComponent(campaignId)}/travel-graph`
  );
}

export function createTravelLocation(
  campaignId: string,
  payload: {
    name: string;
    aliases: string[];
    source_kind: "manual";
    source_ref: null;
    kp_notes: string;
  }
): Promise<TravelLocation> {
  return requestJson<TravelLocation>(
    `/campaigns/${encodeURIComponent(campaignId)}/travel-locations`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function deleteTravelLocation(
  campaignId: string,
  locationId: string
): Promise<{ ok: boolean }> {
  return requestJson<{ ok: boolean }>(
    `/campaigns/${encodeURIComponent(campaignId)}/travel-locations/${encodeURIComponent(locationId)}`,
    { method: "DELETE" }
  );
}

export function createTravelRoute(
  campaignId: string,
  payload: {
    from_location_id: string;
    to_location_id: string;
    travel_minutes: number;
    travel_mode: TravelRoute["travel_mode"];
    bidirectional: boolean;
    status: TravelRoute["status"];
    kp_notes: string;
  }
): Promise<TravelRoute> {
  return requestJson<TravelRoute>(
    `/campaigns/${encodeURIComponent(campaignId)}/travel-routes`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function deleteTravelRoute(
  campaignId: string,
  routeId: string
): Promise<{ ok: boolean }> {
  return requestJson<{ ok: boolean }>(
    `/campaigns/${encodeURIComponent(campaignId)}/travel-routes/${encodeURIComponent(routeId)}`,
    { method: "DELETE" }
  );
}

export function previewTravelRoute(
  campaignId: string,
  payload: { origins: string[]; destination: string; max_minutes: number }
): Promise<TravelRoutePreview> {
  return requestJson<TravelRoutePreview>(
    `/campaigns/${encodeURIComponent(campaignId)}/travel-route-preview`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function listNpcHiddenAppearances(
  campaignId: string
): Promise<NpcHiddenAppearanceResolution[]> {
  return requestJson<NpcHiddenAppearanceResolution[]>(
    `/campaigns/${encodeURIComponent(campaignId)}/npc-hidden-appearances`
  );
}

export function resolveNpcHiddenAppearance(
  campaignId: string,
  payload: {
    idempotency_key: string;
    npc_id: string;
    trigger_text: string;
    appearance_chance: number;
    destinations: HiddenAppearanceDestination[];
    profession_hint: string | null;
  }
): Promise<NpcHiddenAppearanceResolution> {
  return requestJson<NpcHiddenAppearanceResolution>(
    `/campaigns/${encodeURIComponent(campaignId)}/npc-hidden-appearances`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function listRuleReviewCandidates(
  sourceId: string
): Promise<RuleReviewCandidate[]> {
  return requestJson<RuleReviewCandidate[]>(
    `/rulebooks/sources/${encodeURIComponent(sourceId)}/rules?status=review_required`
  );
}

export function reviewRuleCandidate(
  candidateId: string,
  payload: RuleReviewSubmission
): Promise<RuleReviewCandidate> {
  return requestJson<RuleReviewCandidate>(
    `/rulebooks/rules/${encodeURIComponent(candidateId)}/review`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export async function requestFile<T>(url: string, file: File): Promise<T> {
  return requestBinary(url, file, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
}

export async function requestBinary<T>(
  url: string,
  file: File,
  contentType = "application/octet-stream"
): Promise<T> {
  const headers = new Headers();
  headers.set("Content-Type", contentType);
  headers.set("X-File-Name", encodeURIComponent(file.name));
  if (credentials.accessToken) {
    headers.set("Authorization", `Bearer ${credentials.accessToken}`);
  }
  if (credentials.adminToken) {
    headers.set("X-AI-KP-Admin-Token", credentials.adminToken);
  }
  if (credentials.playerToken) {
    headers.set("X-AI-KP-Player-Token", credentials.playerToken);
  }
  const response = await fetchWithTimeout(
    `${apiBase}${url}`,
    { method: "POST", headers, body: file }
  );
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json() as Promise<T>;
}
