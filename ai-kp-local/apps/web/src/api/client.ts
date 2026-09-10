import type {
  ActionAdjudication,
  AiSkillManifest,
  Capability,
  CampaignObjective,
  CampaignWorldEntityGraph,
  CampaignSetupConfig,
  CharacterLifecycleView,
  ConfiguredRegion,
  SettingProfileDocument,
  ModuleRun,
  ModuleSettingAnalysis,
  ModuleSettingProfile,
  ScenarioContractBinding,
  ScenarioContractJob,
  ScenarioSourceScope,
  ScenarioContractValidationReport,
  ScenarioContractVersion,
  ModuleRunDirectorState,
  ModuleRuntimeEntityStatus,
  SceneTransitionInput,
  DirectorAnalysis,
  DirectorHelpAdvice,
  DirectorHelpAuditPage,
  DynamicBranchRun,
  InstalledRuleset,
  ModuleRunStart,
  ModuleRunUpdate,
  NpcReappearanceCandidate,
  ParallelActionAttentionBatch,
  ParallelActionPlayerBatch,
  ParallelActionPlayerRegather,
  CampaignNpcRecord,
  NpcAvailabilityProfile,
  NpcReappearancePolicy,
  HiddenAppearanceDestination,
  InventoryState,
  MemoryClassification,
  MemoryCurationInput,
  MemoryTimelineItem,
  SessionRecapCandidate,
  SessionRecapReviewInput,
  SessionRecapRun,
  SessionZeroView,
  SafeTableMember,
  TableMessage,
  TableMessageAudience,
  SessionContinuityView,
  NpcHiddenAppearanceResolution,
  TravelGraph,
  TravelLocation,
  TravelRoute,
  TravelRoutePreview,
  RuleReviewCandidate,
  RuleReviewSubmission,
  Role,
  RunSettingSelection,
  TurnProposal,
  ManualKernelCandidate,
  WorldExpansionEncounterInput,
  WorldFactCreateInput,
  WorldFactEntry,
  WorldFactType
} from "./types";

export function listSettingCatalogs() {
  return requestJson<import("./types").SettingCatalog[]>("/setting-catalogs");
}

export function listCampaignWorldEntities(
  campaignId: string
): Promise<CampaignWorldEntityGraph> {
  return requestJson<CampaignWorldEntityGraph>(
    `/campaigns/${encodeURIComponent(campaignId)}/world-entities`
  );
}

export function updateCampaignWorldEntityState(
  campaignId: string,
  entityId: string,
  input: import("./types").WorldEntityStateUpdateInput
): Promise<import("./types").WorldEntityStateUpdateResult> {
  return requestJson<import("./types").WorldEntityStateUpdateResult>(
    `/campaigns/${encodeURIComponent(campaignId)}/world-entities/${encodeURIComponent(entityId)}/states`,
    { method: "POST", body: JSON.stringify(input) }
  );
}

export function listModuleSettingProfiles(moduleId: string) {
  return requestJson<ModuleSettingProfile[]>(
    `/modules/${encodeURIComponent(moduleId)}/setting-profiles`
  );
}

export function createModuleSettingProfile(
  moduleId: string,
  input: { title: string; setting_pack_id: string; regions: ConfiguredRegion[] }
) {
  return requestJson<ModuleSettingProfile>(
    `/modules/${encodeURIComponent(moduleId)}/setting-profiles`,
    { method: "POST", body: JSON.stringify(input) }
  );
}

export function updateModuleSettingProfile(
  profileId: string,
  input: { expected_version: number; title: string; document: SettingProfileDocument }
) {
  return requestJson<ModuleSettingProfile>(
    `/setting-profiles/${encodeURIComponent(profileId)}`,
    { method: "PUT", body: JSON.stringify(input) }
  );
}

export function getRunSettingSelection(runId: string) {
  return requestJson<RunSettingSelection | null>(
    `/module-runs/${encodeURIComponent(runId)}/setting-selection`
  );
}

export function setRunSettingSelection(
  runId: string,
  input: {
    expected_run_version: number;
    profile_id: string;
    profile_version: number;
    settlement_id: string;
    reason: string;
  }
) {
  return requestJson<{ run: ModuleRun; selection: RunSettingSelection }>(
    `/module-runs/${encodeURIComponent(runId)}/setting-selection`,
    { method: "PUT", body: JSON.stringify(input) }
  );
}

export function analyzeSettingProfileSettlement(
  profileId: string,
  version: number,
  settlementId: string
) {
  const query = new URLSearchParams({ version: String(version) });
  return requestJson<ModuleSettingAnalysis>(
    `/setting-profiles/${encodeURIComponent(profileId)}/settlements/${encodeURIComponent(settlementId)}/analysis?${query}`
  );
}

export function getCharacterLifecycle(campaignId: string): Promise<CharacterLifecycleView> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/character-lifecycle`);
}

export function proposeCharacterLifecycle(
  campaignId: string,
  input: {
    member_id: string;
    action: "observe" | "replace" | "retire" | "temporary_leave" | "npc_control" | "return" | "resurrect";
    reason: string;
    replacement_investigator_id?: string;
  }
): Promise<unknown> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/character-lifecycle/requests`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function decideCharacterLifecycle(
  requestId: string,
  input: { action: "accept" | "reject"; reason: string; expected_version: number }
): Promise<unknown> {
  return requestJson(`/character-lifecycle/requests/${encodeURIComponent(requestId)}/decision`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function getInventory(campaignId: string): Promise<InventoryState> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/inventory`);
}

export function createInventoryItem(
  campaignId: string,
  input: {
    command_id: string;
    item_type: string;
    public_name: string;
    public_description: string;
    publicly_listed: boolean;
    quantity: number;
    is_unique: boolean;
    holder_kind: "investigator" | "party" | "npc" | "location" | "loot" | "none";
    holder_id: string;
    weight_units?: number;
    unit_value_minor?: number;
    currency_code?: string;
    use_effect?: Record<string, unknown>;
    hidden_properties?: Record<string, unknown>;
    source_refs?: Array<Record<string, unknown>>;
    reason?: string;
  }
): Promise<unknown> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/inventory/items`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function commandInventoryItem(
  itemId: string,
  input: {
    command_id: string;
    command_type: "pickup" | "drop" | "equip" | "unequip" | "consume";
    expected_version: number;
    quantity?: number;
    holder_kind?: "party" | "location" | "loot";
    holder_id?: string;
    equipped_slot?: string;
  }
): Promise<unknown> {
  return requestJson(`/inventory/items/${encodeURIComponent(itemId)}/commands`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function createInventoryOffer(
  campaignId: string,
  input: {
    command_id: string;
    item_id: string;
    expected_item_version: number;
    quantity: number;
    to_investigator_id: string;
  }
): Promise<unknown> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/inventory/offers`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function decideInventoryOffer(
  offerId: string,
  input: {
    command_id: string;
    expected_version: number;
    decision: "accept" | "decline" | "cancel";
  }
): Promise<unknown> {
  return requestJson(`/inventory/offers/${encodeURIComponent(offerId)}/decisions`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function tradeInventoryItem(
  campaignId: string,
  input: {
    command_id: string;
    direction: "purchase" | "sell";
    item_id: string;
    expected_item_version: number;
    quantity: number;
    investigator_id: string;
    counterparty_kind: "npc" | "vendor";
    counterparty_id: string;
    currency_code: string;
    expected_investigator_balance_version: number | null;
    expected_counterparty_balance_version: number | null;
  }
): Promise<unknown> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/inventory/trades`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function listTableMembers(campaignId: string): Promise<SafeTableMember[]> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/table-members`);
}

export function listTableMessages(campaignId: string): Promise<TableMessage[]> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/messages`);
}

export function sendTableMessage(
  campaignId: string,
  input: {
    audience: TableMessageAudience;
    content: string;
    recipient_member_id: string | null;
    client_message_id: string;
  }
): Promise<TableMessage> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/messages`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function getSessionContinuity(campaignId: string): Promise<SessionContinuityView> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/continuity`);
}

export function listCampaignObjectives(campaignId: string): Promise<CampaignObjective[]> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/objectives`);
}

export function createCampaignObjective(
  campaignId: string,
  input: {
    command_id: string;
    title: string;
    public_description: string;
    kp_notes: string;
    visibility: "table" | "kp";
    source_refs: Array<Record<string, unknown>>;
  }
): Promise<CampaignObjective> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/objectives`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function updateCampaignObjective(
  objectiveId: string,
  input: {
    command_id: string;
    expected_version: number;
    status: CampaignObjective["status"];
    public_progress: string;
    kp_notes: string;
    source_refs: Array<Record<string, unknown>>;
  }
): Promise<CampaignObjective> {
  return requestJson(`/objectives/${encodeURIComponent(objectiveId)}/commands`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function endSessionEpisode(
  campaignId: string,
  clientEndId: string
): Promise<SessionContinuityView> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/session-end`, {
    method: "POST",
    body: JSON.stringify({ client_end_id: clientEndId })
  });
}

export function continueCampaign(
  campaignId: string,
  clientContinueId: string
): Promise<SessionContinuityView> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/continue`, {
    method: "POST",
    body: JSON.stringify({ client_continue_id: clientContinueId })
  });
}

export function transitionSessionEpisode(
  campaignId: string,
  target: "paused" | "in_progress",
  expectedVersion: number
): Promise<SessionContinuityView> {
  return requestJson(`/campaigns/${encodeURIComponent(campaignId)}/episode/transition`, {
    method: "POST",
    body: JSON.stringify({ target, expected_version: expectedVersion })
  });
}

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

async function requestWithTimeout<T>(
  input: RequestInfo | URL,
  init: RequestInit = {},
  consume: (response: Response) => Promise<T>,
  timeoutMs = defaultRequestTimeoutMs
): Promise<T> {
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
    const response = await fetch(input, { ...init, signal: controller.signal });
    // Keep the timeout and abort signal alive until the response body has been
    // consumed. Fetch resolves after headers, while json/text/blob can still
    // block indefinitely on a stalled or truncated response stream.
    return await consume(response);
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
  return requestWithTimeout(`${apiBase}${url}`, { ...init, headers }, async (response) => {
    if (!response.ok) {
      throw await responseError(response);
    }
    return response.json() as Promise<T>;
  });
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
  return requestWithTimeout(`${apiBase}${url}`, { headers, signal }, async (response) => {
    if (!response.ok) {
      throw await responseError(response);
    }
    return response.blob();
  });
}

export function fetchCapabilities(): Promise<Capability[]> {
  return requestJson<Capability[]>("/capabilities");
}

export function fetchInstalledRulesets(): Promise<InstalledRuleset[]> {
  return requestJson<InstalledRuleset[]>("/rulesets");
}

export function fetchInstalledAiSkills(): Promise<AiSkillManifest[]> {
  return requestJson<AiSkillManifest[]>("/ai-skills");
}

export function settleParallelPlayerActions(
  campaignId: string,
  payload: { action_ids: string[] }
): Promise<{
  status:
    | "awaiting_confirmation"
    | "awaiting_checks"
    | "ready"
    | "settled"
    | "needs_attention";
  batch: Record<string, unknown> | null;
  actions: unknown[];
  checks: unknown[];
  unsupported: unknown[];
  message: string;
}> {
  return requestJson(
    `/campaigns/${encodeURIComponent(campaignId)}/actions/settle`,
    {
      method: "POST",
      body: JSON.stringify(payload)
    }
  );
}

export function getCurrentParallelActionPlayerBatch(
  campaignId: string
): Promise<ParallelActionPlayerBatch | null> {
  return requestJson<ParallelActionPlayerBatch | null>(
    `/campaigns/${encodeURIComponent(campaignId)}/parallel-action-batches/current`
  );
}

export function getCurrentParallelActionPlayerRegather(
  campaignId: string
): Promise<ParallelActionPlayerRegather | null> {
  return requestJson<ParallelActionPlayerRegather | null>(
    `/campaigns/${encodeURIComponent(campaignId)}/parallel-action-regathers/current`
  );
}

export function listParallelActionAttentionBatches(
  campaignId: string
): Promise<ParallelActionAttentionBatch[]> {
  return requestJson<ParallelActionAttentionBatch[]>(
    `/campaigns/${encodeURIComponent(campaignId)}/parallel-action-batches?status=needs_attention`
  );
}

export function resumeParallelActionBatch(
  batchId: string,
  payload: { expected_version: number; reason: string }
): Promise<Record<string, unknown>> {
  return requestJson(
    `/parallel-action-batches/${encodeURIComponent(batchId)}/resume`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function abandonParallelActionBatch(
  batchId: string,
  payload: { expected_version: number; reason: string }
): Promise<Record<string, unknown>> {
  return requestJson(
    `/parallel-action-batches/${encodeURIComponent(batchId)}/abandon`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function listManualKernelCandidates(actionId: string): Promise<{
  action_id: string;
  action_text: string;
  candidates: ManualKernelCandidate[];
}> {
  return requestJson(
    `/player-actions/${encodeURIComponent(actionId)}/kernel-candidates`
  );
}

export function prepareManualKernelSelection(
  actionId: string,
  payload: { operator_id: string; requested_skill_key?: string | null }
): Promise<{
  proposal: TurnProposal;
  adjudication: ActionAdjudication;
  preview_hash: string;
  message: string;
}> {
  return requestJson(
    `/player-actions/${encodeURIComponent(actionId)}/kernel-selection`,
    { method: "POST", body: JSON.stringify(payload) }
  );
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

export function getCurrentModuleRun(
  campaignId: string,
  options: { signal?: AbortSignal } = {}
): Promise<ModuleRun | null> {
  return requestJson<ModuleRun | null>(
    `/campaigns/${encodeURIComponent(campaignId)}/module-runs/current`,
    { signal: options.signal }
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

export function listScenarioContracts(
  moduleId: string
): Promise<ScenarioContractVersion[]> {
  return requestJson<ScenarioContractVersion[]>(
    `/modules/${encodeURIComponent(moduleId)}/scenario-contracts`
  );
}

export function generateScenarioContract(
  moduleId: string,
  rulesetId = "coc7",
  sourceScopeKey?: string
): Promise<ScenarioContractJob> {
  return requestJson<ScenarioContractJob>(
    `/modules/${encodeURIComponent(moduleId)}/scenario-contracts/generate`,
    {
      method: "POST",
      body: JSON.stringify({
        ruleset_id: rulesetId,
        ...(sourceScopeKey ? { source_scope_key: sourceScopeKey } : {})
      })
    }
  );
}

export function listScenarioSourceScopes(
  moduleId: string
): Promise<ScenarioSourceScope[]> {
  return requestJson<ScenarioSourceScope[]>(
    `/modules/${encodeURIComponent(moduleId)}/scenario-source-scopes`
  );
}

export function listScenarioContractJobs(
  moduleId: string
): Promise<ScenarioContractJob[]> {
  return requestJson<ScenarioContractJob[]>(
    `/modules/${encodeURIComponent(moduleId)}/scenario-contract-jobs`
  );
}

export function retryScenarioContractJob(jobId: string): Promise<ScenarioContractJob> {
  return requestJson<ScenarioContractJob>(
    `/scenario-contract-jobs/${encodeURIComponent(jobId)}/retry`,
    { method: "POST" }
  );
}

export function compileScenarioContract(
  moduleId: string,
  contract: Record<string, unknown>
): Promise<{
  compilation: { report: ScenarioContractValidationReport };
  version: ScenarioContractVersion | null;
}> {
  return requestJson(
    `/modules/${encodeURIComponent(moduleId)}/scenario-contracts/compile`,
    { method: "POST", body: JSON.stringify({ contract }) }
  );
}

export function publishScenarioContract(
  versionId: string,
  expectedRowVersion: number
): Promise<ScenarioContractVersion> {
  return requestJson<ScenarioContractVersion>(
    `/scenario-contracts/${encodeURIComponent(versionId)}/publish`,
    {
      method: "POST",
      body: JSON.stringify({ expected_row_version: expectedRowVersion })
    }
  );
}

export function getScenarioContractBinding(
  runId: string
): Promise<ScenarioContractBinding | null> {
  return requestJson<ScenarioContractBinding | null>(
    `/module-runs/${encodeURIComponent(runId)}/scenario-contract-binding`
  );
}

export function bindScenarioContract(
  runId: string,
  contractVersionId: string
): Promise<ScenarioContractBinding> {
  return requestJson<ScenarioContractBinding>(
    `/module-runs/${encodeURIComponent(runId)}/scenario-contract-binding`,
    {
      method: "POST",
      body: JSON.stringify({ contract_version_id: contractVersionId })
    }
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

export function askDirectorHelp(
  runId: string,
  question: string,
  options: { signal?: AbortSignal } = {}
): Promise<DirectorHelpAdvice> {
  return requestJson<DirectorHelpAdvice>(
    `/module-runs/${encodeURIComponent(runId)}/director/help`,
    {
      method: "POST",
      body: JSON.stringify({ question }),
      signal: options.signal
    }
  );
}

export function listDirectorHelpAudits(
  campaignId: string,
  options: { limit?: number; beforeId?: string; signal?: AbortSignal } = {}
): Promise<DirectorHelpAuditPage> {
  const params = new URLSearchParams();
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.beforeId !== undefined) params.set("before_id", options.beforeId);
  const query = params.size ? `?${params.toString()}` : "";
  return requestJson<DirectorHelpAuditPage>(
    `/campaigns/${encodeURIComponent(campaignId)}/director-help/audits${query}`,
    { signal: options.signal }
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

export function updateAutomationLevel(
  runId: string,
  payload: {
    expected_version: number;
    level: NonNullable<ModuleRun["automation_level"]>;
    reason: string;
  }
): Promise<ModuleRun> {
  return requestJson<ModuleRun>(
    `/module-runs/${encodeURIComponent(runId)}/automation`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

type WorldExpansionProposalPayload = {
  player_intent: string;
  requested_expansion_kind?: "environment" | "reactive_branch" | "anchor_bridge";
  pc_id?: string | null;
  map_id?: string | null;
  setting_pack_id?: string | null;
  settlement_kind?: "city" | "town" | "village" | "rural" | null;
};

type AutoWorldExpansionResult = {
  status: "materialized" | "needs_attention" | "failed";
  proposal: TurnProposal | null;
  materialization: unknown;
  policy: unknown;
  message: string;
};

export function generateWorldExpansionProposal(
  runId: string,
  payload: WorldExpansionProposalPayload & { auto_materialize: true }
): Promise<AutoWorldExpansionResult>;
export function generateWorldExpansionProposal(
  runId: string,
  payload: WorldExpansionProposalPayload & { auto_materialize?: false }
): Promise<TurnProposal>;
export function generateWorldExpansionProposal(
  runId: string,
  payload: WorldExpansionProposalPayload & { auto_materialize?: boolean }
): Promise<TurnProposal | AutoWorldExpansionResult> {
  return requestJson<TurnProposal | AutoWorldExpansionResult>(
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

export function getSessionZero(campaignId: string): Promise<SessionZeroView> {
  return requestJson<SessionZeroView>(
    `/campaigns/${encodeURIComponent(campaignId)}/session-zero`
  );
}

export function saveSessionZeroConfig(
  campaignId: string,
  payload: CampaignSetupConfig & { expected_version: number }
): Promise<SessionZeroView> {
  return requestJson<SessionZeroView>(
    `/campaigns/${encodeURIComponent(campaignId)}/session-zero/config`,
    { method: "PUT", body: JSON.stringify(payload) }
  );
}

export function saveSessionZeroPreferences(
  campaignId: string,
  payload: {
    expected_version: number;
    public_style: Record<string, number | string>;
    private_style: Record<string, number | string>;
    lines: string[];
    veils: string[];
  }
): Promise<SessionZeroView> {
  return requestJson<SessionZeroView>(
    `/campaigns/${encodeURIComponent(campaignId)}/session-zero/preferences`,
    { method: "PUT", body: JSON.stringify(payload) }
  );
}

export function confirmSessionZero(
  campaignId: string,
  revisionId: string,
  expectedVersion: number
): Promise<SessionZeroView> {
  return requestJson<SessionZeroView>(
    `/campaigns/${encodeURIComponent(campaignId)}/session-zero/confirm`,
    {
      method: "POST",
      body: JSON.stringify({ revision_id: revisionId, expected_version: expectedVersion })
    }
  );
}

export function triggerSessionSafety(
  campaignId: string,
  responseKind: "pause" | "fade" | "change" | "rewind"
): Promise<{ event: Record<string, unknown>; session_zero: SessionZeroView }> {
  return requestJson(
    `/campaigns/${encodeURIComponent(campaignId)}/safety-tool`,
    { method: "POST", body: JSON.stringify({ response_kind: responseKind }) }
  );
}

export function resolveSessionSafety(
  campaignId: string,
  eventId: string,
  resolutionKind: "fade" | "change" | "rewind" | "resume"
): Promise<{ event: Record<string, unknown>; session_zero: SessionZeroView }> {
  return requestJson(
    `/campaigns/${encodeURIComponent(campaignId)}/safety-tool/${encodeURIComponent(eventId)}/resolve`,
    { method: "POST", body: JSON.stringify({ resolution_kind: resolutionKind }) }
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
  return requestWithTimeout(
    `${apiBase}${url}`,
    { method: "POST", headers, body: file },
    async (response) => {
      if (!response.ok) {
        throw await responseError(response);
      }
      return response.json() as Promise<T>;
    }
  );
}
