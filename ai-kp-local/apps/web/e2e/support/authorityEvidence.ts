import type { Page, Response } from "@playwright/test";

export const AUTHORITY_REF_KEYS = [
  "campaign_ids",
  "session_ids",
  "episode_ids",
  "continuity_snapshot_ids",
  "member_ids",
  "investigator_ids",
  "module_import_job_ids",
  "module_ids",
  "module_run_ids",
  "scenario_contract_job_ids",
  "scenario_contract_version_ids",
  "action_ids",
  "adjudication_ids",
  "check_ids",
  "parallel_batch_ids",
  "public_turn_ids",
  "scenario_command_batch_ids"
] as const;

export type AuthorityRefKey = typeof AUTHORITY_REF_KEYS[number];
export type AuthorityRefs = Record<AuthorityRefKey, string[]>;
export type ContinuityAuthorityRef = {
  episodeId: string;
  snapshotId: string;
};

const NAMED_ID_FIELDS: Readonly<Record<string, AuthorityRefKey>> = {
  campaign_id: "campaign_ids",
  session_id: "session_ids",
  episode_id: "episode_ids",
  snapshot_id: "continuity_snapshot_ids",
  member_id: "member_ids",
  investigator_id: "investigator_ids",
  module_id: "module_ids",
  run_id: "module_run_ids",
  contract_version_id: "scenario_contract_version_ids",
  action_id: "action_ids",
  player_action_id: "action_ids",
  adjudication_id: "adjudication_ids",
  check_id: "check_ids",
  batch_id: "parallel_batch_ids",
  scenario_command_batch_id: "scenario_command_batch_ids",
  public_turn_id: "public_turn_ids"
};

function topLevelIdKind(pathname: string): AuthorityRefKey | undefined {
  if (/\/api\/campaigns\/?$/.test(pathname)) return "campaign_ids";
  if (/\/api\/(?:campaigns\/[^/]+\/)?sessions\/?$/.test(pathname)) return "session_ids";
  if (/\/api\/campaigns\/[^/]+\/module-imports\/?$/.test(pathname)) {
    return "module_import_job_ids";
  }
  if (/\/api\/campaigns\/[^/]+\/module-runs(?:\/play-state)?\/?$/.test(pathname)) {
    return "module_run_ids";
  }
  if (/\/api\/(?:modules\/[^/]+\/)?scenario-contract-jobs\/?$/.test(pathname)) {
    return "scenario_contract_job_ids";
  }
  if (/\/api\/modules\/[^/]+\/scenario-contracts\/?$/.test(pathname)) {
    return "scenario_contract_version_ids";
  }
  if (/\/api\/campaigns\/[^/]+\/actions\/?$/.test(pathname)) return "action_ids";
  if (/\/api\/campaigns\/[^/]+\/checks\/?$/.test(pathname)) return "check_ids";
  if (/\/api\/campaigns\/[^/]+\/parallel-action-batches\/current\/?$/.test(pathname)) {
    return "parallel_batch_ids";
  }
  if (/\/api\/campaigns\/[^/]+\/public-turns\/?$/.test(pathname)) {
    return "public_turn_ids";
  }
  return undefined;
}

function add(refs: Map<AuthorityRefKey, Set<string>>, key: AuthorityRefKey, value: unknown) {
  if (typeof value === "string" && value.trim()) refs.get(key)?.add(value);
}

function shouldProjectTopLevelId(key: AuthorityRefKey, value: Record<string, unknown>): boolean {
  if (key === "scenario_contract_job_ids") return value.status === "succeeded";
  if (key === "scenario_contract_version_ids") return value.status === "published";
  return true;
}

function projectNamedIds(refs: Map<AuthorityRefKey, Set<string>>, value: unknown): void {
  if (Array.isArray(value)) {
    for (const item of value) projectNamedIds(refs, item);
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [field, nested] of Object.entries(value)) {
    const target = NAMED_ID_FIELDS[field];
    if (target) add(refs, target, nested);
    projectNamedIds(refs, nested);
  }
}

function projectEndpointIds(
  refs: Map<AuthorityRefKey, Set<string>>,
  pathname: string,
  payload: unknown
): void {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return;
  const record = payload as Record<string, unknown>;
  const campaign = record.campaign;
  if (campaign && typeof campaign === "object") {
    add(refs, "campaign_ids", (campaign as { id?: unknown }).id);
  }
  const session = record.session;
  if (session && typeof session === "object") {
    add(refs, "session_ids", (session as { id?: unknown }).id);
  }
  const currentEpisode = record.current_episode;
  if (currentEpisode && typeof currentEpisode === "object") {
    add(refs, "episode_ids", (currentEpisode as { id?: unknown }).id);
  }
  const latestSnapshot = record.latest_snapshot;
  if (latestSnapshot && typeof latestSnapshot === "object") {
    add(refs, "continuity_snapshot_ids", (latestSnapshot as { id?: unknown }).id);
  }
  const member = record.member;
  if (member && typeof member === "object") {
    add(refs, "member_ids", (member as { id?: unknown }).id);
  }
  if (/\/api\/campaigns\/[^/]+\/actions\/?$/.test(pathname)) {
    const action = record.player_action;
    if (action && typeof action === "object") add(refs, "action_ids", (action as { id?: unknown }).id);
  }
  const adjudication = record.adjudication;
  if (adjudication && typeof adjudication === "object") {
    add(refs, "adjudication_ids", (adjudication as { id?: unknown }).id);
  }
  const checks = record.checks;
  if (Array.isArray(checks)) {
    for (const check of checks) {
      if (check && typeof check === "object") add(refs, "check_ids", (check as { id?: unknown }).id);
    }
  }
  const binding = record.binding;
  if (binding && typeof binding === "object") {
    projectNamedIds(refs, binding);
  }
}

/**
 * Records only endpoint-scoped authority identifiers. Response bodies are
 * inspected in memory and immediately discarded; credentials, headers, browser
 * storage, invitation codes and response text are never retained.
 */
export class AuthorityRefCollector {
  private readonly refs = new Map<AuthorityRefKey, Set<string>>(
    AUTHORITY_REF_KEYS.map((key) => [key, new Set<string>()])
  );
  private readonly pending = new Set<Promise<void>>();
  private observationSequence = 0;
  private latestContinuitySequence = 0;
  private latestContinuityRef: ContinuityAuthorityRef | null = null;

  attach(page: Page): void {
    page.on("response", (response) => {
      const sequence = ++this.observationSequence;
      const observation = this.observe(response, sequence).finally(
        () => this.pending.delete(observation)
      );
      this.pending.add(observation);
    });
  }

  private async observe(response: Response, sequence: number): Promise<void> {
    if (!response.ok()) return;
    const url = new URL(response.url());
    if (!url.pathname.startsWith("/api/")) return;
    const method = response.request().method();
    const campaignIds = this.refs.get("campaign_ids") ?? new Set<string>();
    const moduleIds = this.refs.get("module_ids") ?? new Set<string>();
    const isCreateCampaign = method === "POST" && /\/api\/campaigns\/?$/.test(url.pathname);
    const isMutation = ["POST", "PUT", "PATCH"].includes(method);
    const isObservedCampaignRead = method === "GET" && [...campaignIds].some(
      (id) => url.pathname.includes(`/campaigns/${encodeURIComponent(id)}/`)
        || url.pathname.includes(`/campaigns/${id}/`)
    );
    const isObservedModuleRead = method === "GET" && [...moduleIds].some(
      (id) => url.pathname.includes(`/modules/${encodeURIComponent(id)}/`)
        || url.pathname.includes(`/modules/${id}/`)
    );
    if (!isCreateCampaign && !isMutation && !isObservedCampaignRead && !isObservedModuleRead) {
      return;
    }
    const contentType = response.headers()["content-type"] ?? "";
    if (!contentType.includes("application/json")) return;
    const payload = await response.json().catch(() => null) as unknown;
    if (payload === null) return;
    const topLevelKind = topLevelIdKind(url.pathname);
    if (topLevelKind) {
      if (Array.isArray(payload)) {
        for (const item of payload) {
          if (item && typeof item === "object" && shouldProjectTopLevelId(
            topLevelKind,
            item as Record<string, unknown>
          )) add(this.refs, topLevelKind, (item as { id?: unknown }).id);
        }
      } else if (typeof payload === "object") {
        if (shouldProjectTopLevelId(topLevelKind, payload as Record<string, unknown>)) {
          add(this.refs, topLevelKind, (payload as { id?: unknown }).id);
        }
      }
    }
    projectEndpointIds(this.refs, url.pathname, payload);
    projectNamedIds(this.refs, payload);
    if (payload && typeof payload === "object" && !Array.isArray(payload)) {
      const record = payload as Record<string, unknown>;
      const episode = record.current_episode;
      const snapshot = record.latest_snapshot;
      const episodeId = episode && typeof episode === "object"
        ? (episode as { id?: unknown }).id
        : undefined;
      const snapshotId = snapshot && typeof snapshot === "object"
        ? (snapshot as { id?: unknown }).id
        : undefined;
      if (
        typeof episodeId === "string"
        && episodeId.trim()
        && typeof snapshotId === "string"
        && snapshotId.trim()
        && sequence >= this.latestContinuitySequence
      ) {
        this.latestContinuitySequence = sequence;
        this.latestContinuityRef = { episodeId, snapshotId };
      }
    }
  }

  async snapshot(): Promise<AuthorityRefs> {
    while (this.pending.size > 0) await Promise.all([...this.pending]);
    return Object.fromEntries(
      AUTHORITY_REF_KEYS.map((key) => [key, [...(this.refs.get(key) ?? [])].sort()])
    ) as AuthorityRefs;
  }

  async latestContinuity(): Promise<ContinuityAuthorityRef | null> {
    while (this.pending.size > 0) await Promise.all([...this.pending]);
    return this.latestContinuityRef ? { ...this.latestContinuityRef } : null;
  }
}
