import type { Page } from "@playwright/test";
import {
  advancePlayerDriverHistory,
  EMPTY_PLAYER_DRIVER_HISTORY,
  planAdaptivePlayerAction,
  recordPlayerDriverAction,
  type PlayerDriverDecision,
  type PlayerDriverHistory,
  type PlayerDriverModel,
  type PlayerDriverModelRequest,
  type PlayerVisibleObservation,
  type PlayerVisibleTurn
} from "../../src/evaluation/adaptivePlayerDriver";
import type { PlayerPersona } from "./playerAgent";

const MODEL_RESPONSE_BYTE_LIMIT = 16 * 1024;
const PLAYER_VISIBLE_TEXT_LIMIT = 2_000;

function boundedVisibleText(value: string | null | undefined): string {
  return (value ?? "").replace(/\s+/g, " ").trim().slice(0, PLAYER_VISIBLE_TEXT_LIMIT);
}

async function currentVisibleLocation(page: Page): Promise<string | null> {
  const continuity = page.getByText(/^当前位置：/).first();
  if (await continuity.isVisible().catch(() => false)) {
    return boundedVisibleText(await continuity.textContent()).replace(/^当前位置：/, "") || null;
  }
  const mapLocation = page.locator('[aria-label*="（当前位置）"]').first();
  if (await mapLocation.isVisible().catch(() => false)) {
    return boundedVisibleText(await mapLocation.getAttribute("aria-label"))
      .replace(/（当前位置）$/, "") || null;
  }
  return null;
}

async function visiblePublicTurns(page: Page): Promise<PlayerVisibleTurn[]> {
  const cards = page.locator(".public-turn-card");
  const count = await cards.count();
  const turns: PlayerVisibleTurn[] = [];
  for (let index = Math.max(0, count - 4); index < count; index += 1) {
    const card = cards.nth(index);
    turns.push({
      playerAction: boundedVisibleText(await card.locator("small").first().textContent().catch(() => "")),
      publicNarration: boundedVisibleText(
        (await card.locator("p").allTextContents()).join(" ")
      )
    });
  }
  return turns;
}

async function visibleObjectives(page: Page): Promise<string[]> {
  const cards = page.locator(".campaign-objective-panel .objective-card");
  const objectives: string[] = [];
  for (let index = 0; index < Math.min(await cards.count(), 4); index += 1) {
    const text = boundedVisibleText(await cards.nth(index).innerText());
    if (text) objectives.push(text);
  }
  return objectives;
}

async function visibleCheck(page: Page): Promise<{
  outcome: PlayerVisibleObservation["visibleCheckOutcome"];
  summary: string | null;
}> {
  const failed = page.locator(".check-card.failed").last();
  if (await failed.isVisible().catch(() => false)) {
    return {
      outcome: "failure",
      summary: boundedVisibleText(await failed.innerText()) || null
    };
  }
  const succeeded = page.locator(".check-card.succeeded, .check-card.success").last();
  if (await succeeded.isVisible().catch(() => false)) {
    return {
      outcome: "success",
      summary: boundedVisibleText(await succeeded.innerText()) || null
    };
  }
  return { outcome: null, summary: null };
}

export async function collectPlayerVisibleObservation(
  page: Page
): Promise<PlayerVisibleObservation> {
  let check = await visibleCheck(page);
  const checkTab = page.getByRole("tab", { name: "检定" }).first();
  if (check.outcome === null && await checkTab.isVisible().catch(() => false)) {
    await checkTab.click({ timeout: 10_000 });
    check = await visibleCheck(page);
  }
  const actionTab = page.getByRole("tab", { name: "行动" }).first();
  if (await actionTab.isVisible().catch(() => false)) {
    await actionTab.click({ timeout: 10_000 });
  }
  const [currentLocation, publicObjectives, recentTurns] = await Promise.all([
    currentVisibleLocation(page),
    visibleObjectives(page),
    visiblePublicTurns(page)
  ]);
  return {
    currentLocation,
    publicObjectives,
    recentTurns,
    visibleCheckOutcome: check.outcome,
    visibleCheckSummary: check.summary
  };
}

async function boundedResponseText(response: Response): Promise<string> {
  const declared = Number(response.headers.get("content-length") ?? "0");
  if (Number.isFinite(declared) && declared > MODEL_RESPONSE_BYTE_LIMIT) {
    throw new Error("player model response exceeded byte limit");
  }
  if (!response.body) return "";
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let total = 0;
  let text = "";
  while (true) {
    const next = await reader.read();
    if (next.done) break;
    total += next.value.byteLength;
    if (total > MODEL_RESPONSE_BYTE_LIMIT) {
      await reader.cancel();
      throw new Error("player model response exceeded byte limit");
    }
    text += decoder.decode(next.value, { stream: true });
  }
  return text + decoder.decode();
}

function chatCompletionsUrl(baseUrl: string): string {
  const normalized = baseUrl.trim().replace(/\/+$/, "");
  return normalized.endsWith("/chat/completions")
    ? normalized
    : `${normalized}/chat/completions`;
}

export function createOpenAiCompatiblePlayerDriverModel(config: {
  baseUrl: string;
  apiKey: string;
  modelId: string;
  timeoutMs?: number;
}): PlayerDriverModel {
  return async (request: PlayerDriverModelRequest) => {
    const isGptOss = config.modelId.toLocaleLowerCase().includes("gpt-oss");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), config.timeoutMs ?? 20_000);
    try {
      const response = await fetch(chatCompletionsUrl(config.baseUrl), {
        method: "POST",
        headers: {
          ...(config.apiKey ? { Authorization: `Bearer ${config.apiKey}` } : {}),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          model: config.modelId,
          messages: [
            { role: "system", content: request.system },
            { role: "user", content: request.user }
          ],
          max_tokens: isGptOss ? 1_024 : 320,
          temperature: isGptOss ? 1 : 0.2,
          ...(isGptOss
            ? {
                top_p: 1,
                response_format: { type: "json_object" },
                chat_template_kwargs: { reasoning_effort: "low" }
              }
            : {})
        }),
        signal: controller.signal
      });
      const raw = await boundedResponseText(response);
      if (!response.ok) {
        throw new Error(`player model HTTP ${response.status}`);
      }
      const payload: unknown = JSON.parse(raw);
      const content = (
        payload as { choices?: Array<{ message?: { content?: unknown } }> }
      ).choices?.[0]?.message?.content;
      if (typeof content !== "string") {
        throw new Error("player model response omitted message content");
      }
      return content;
    } finally {
      clearTimeout(timer);
    }
  };
}

export function configuredPlayerDriverModel(): PlayerDriverModel | undefined {
  const baseUrl = process.env.AI_KP_REALCASE_PLAYER_BASE_URL?.trim();
  const apiKey = process.env.AI_KP_REALCASE_PLAYER_API_KEY?.trim();
  const modelId = process.env.AI_KP_REALCASE_PLAYER_MODEL?.trim();
  const configured = [baseUrl, modelId].filter(Boolean).length;
  if (configured === 0) return undefined;
  if (configured !== 2) {
    throw new Error(
      "AI_KP_REALCASE_PLAYER_BASE_URL and MODEL must be configured together"
    );
  }
  return createOpenAiCompatiblePlayerDriverModel({
    baseUrl: baseUrl!,
    apiKey: apiKey ?? "",
    modelId: modelId!
  });
}

function personaStyle(persona: PlayerPersona): string {
  return [
    `行动风格：${persona.intents[0] ?? "谨慎、具体"}`,
    `追问时：${persona.clarification}`,
    `失败策略：${persona.pushPolicy}`
  ].join(" ");
}

export class AdaptivePlayerDriver {
  private history: PlayerDriverHistory = EMPTY_PLAYER_DRIVER_HISTORY;
  private lastVisibleCheckSummary: string | null = null;

  constructor(private readonly model?: PlayerDriverModel) {}

  async nextAction(page: Page, persona: PlayerPersona): Promise<PlayerDriverDecision> {
    const collected = await collectPlayerVisibleObservation(page);
    const staleCheck = collected.visibleCheckSummary !== null
      && collected.visibleCheckSummary === this.lastVisibleCheckSummary;
    if (collected.visibleCheckSummary !== null) {
      this.lastVisibleCheckSummary = collected.visibleCheckSummary;
    }
    const observation = staleCheck
      ? {
          ...collected,
          visibleCheckOutcome: null,
          visibleCheckSummary: null
        }
      : collected;
    const observedHistory = advancePlayerDriverHistory(this.history, observation);
    const decision = await planAdaptivePlayerAction(
      observation,
      { id: persona.id, style: personaStyle(persona) },
      observedHistory,
      this.model
    );
    this.history = recordPlayerDriverAction(observedHistory, decision.action);
    return decision;
  }
}
