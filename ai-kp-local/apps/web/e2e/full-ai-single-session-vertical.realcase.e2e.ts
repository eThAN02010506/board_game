import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { closeFullAiCampaign, createFullAiCampaign } from "./support/fullAiCampaign";
import { AuthorityRefCollector } from "./support/authorityEvidence";
import {
  AdaptivePlayerDriver,
  configuredPlayerDriverModel
} from "./support/adaptivePlayerDriver";
import { ManagedBackendProcess } from "./support/backendProcess";
import {
  configureRealCaseModel,
  endFullAiSession,
  importAndStartFullAiModule,
  playParallelPublishedOperatorCohort
} from "./support/fullAiRealCaseJourney";
import {
  playPlayerTurn,
  REAL_PLAYER_PERSONAS
} from "./support/playerAgent";

function configuredModulePath(): string | undefined {
  const single = process.env.AI_KP_REALCASE_MODULE_PATH;
  if (single?.trim()) return path.resolve(single.trim());
  const encoded = process.env.AI_KP_REALCASE_MODULE_PATHS_JSON;
  if (!encoded) return undefined;
  const parsed: unknown = JSON.parse(encoded);
  if (!Array.isArray(parsed) || parsed.some((item) => typeof item !== "string" || !item.trim())) {
    throw new Error("AI_KP_REALCASE_MODULE_PATHS_JSON must be a JSON array of paths");
  }
  return parsed.length > 0 ? path.resolve(parsed[0].trim()) : undefined;
}

function configuredMaxTurns(): number {
  const value = Number(process.env.AI_KP_REALCASE_MAX_TURNS ?? "20");
  if (!Number.isInteger(value) || value < 0 || value > 32) {
    throw new Error("AI_KP_REALCASE_MAX_TURNS must be an integer from 0 through 32");
  }
  return value;
}

const modulePath = configuredModulePath();
const maxTurns = configuredMaxTurns();
const modelBaseUrl = process.env.AI_KP_REALCASE_BASE_URL;
const modelApiKey = process.env.AI_KP_REALCASE_API_KEY;
const modelId = process.env.AI_KP_REALCASE_MODEL;
const useSavedModel = process.env.AI_KP_REALCASE_USE_SAVED_MODEL === "1";
const evidencePath = process.env.AI_KP_REALCASE_VERTICAL_EVIDENCE_PATH;
const sourceCommit = process.env.AI_KP_REALCASE_SOURCE_COMMIT;
const managedBackend = process.env.AI_KP_PLAYWRIGHT_MANAGED_BACKEND === "1";
const backendPort = Number(process.env.AI_KP_PLAYWRIGHT_BACKEND_PORT ?? "8012");
const frontendPort = Number(process.env.AI_KP_PLAYWRIGHT_FRONTEND_PORT ?? "5174");
const databasePath = process.env.AI_KP_PLAYWRIGHT_DB_PATH;
if (sourceCommit && !/^[0-9a-f]{7,40}$/.test(sourceCommit)) {
  throw new Error("AI_KP_REALCASE_SOURCE_COMMIT must be a 7..40 character lowercase Git hash");
}
if (managedBackend && (!databasePath || !path.isAbsolute(databasePath))) {
  throw new Error("Managed vertical smoke requires an absolute AI_KP_PLAYWRIGHT_DB_PATH");
}
if (!Number.isInteger(backendPort) || backendPort < 1 || backendPort > 65_535) {
  throw new Error("AI_KP_PLAYWRIGHT_BACKEND_PORT must be a valid TCP port");
}
if (!Number.isInteger(frontendPort) || frontendPort < 1 || frontendPort > 65_535) {
  throw new Error("AI_KP_PLAYWRIGHT_FRONTEND_PORT must be a valid TCP port");
}

test.skip(
  !modulePath || (!useSavedModel && (!modelBaseUrl || !modelId)),
  "Set MODULE_PATH and either USE_SAVED_MODEL=1 or BASE_URL/MODEL; API_KEY is optional for local services"
);
test.describe.configure({ mode: "serial" });
test.use({ trace: "off", video: "off" });
test.setTimeout(4 * 60 * 60 * 1000);

let backendManager: ManagedBackendProcess | null = null;
test.afterEach(async () => {
  await backendManager?.cleanup();
  backendManager = null;
});

function reportStage(stage: string) {
  console.log(`[VERTICAL-SMOKE] ${stage}`);
}

test("four real player UIs complete one Full AI Session vertical smoke", async ({
  browser
}) => {
  if (managedBackend) {
    backendManager = new ManagedBackendProcess({
      port: backendPort,
      databasePath: databasePath!,
      frontendUrl: `http://127.0.0.1:${frontendPort}`
    });
    await backendManager.start();
  }
  const startedAt = new Date().toISOString();
  const authorityRefs = new AuthorityRefCollector();
  const campaign = await createFullAiCampaign(browser, {
    campaignTitle: `Vertical Full AI ${Date.now().toString(36)}`,
    playerCount: 4,
    worldview: "单次真实跑团纵切；允许自由行动，但事实、检定、代价与状态改变必须经过权威流程",
    authorityRefs,
    authorizeKpPage: backendManager
      ? (page) => backendManager!.authorizeAdministratorPage(page)
      : undefined,
    reportStage
  });
  const evidence: Record<string, unknown> = {
    evidence_kind: "single_session_vertical_smoke",
    started_at: startedAt,
    mode: "ai_kp",
    semantic_profile: "small",
    player_count: campaign.players.length,
    max_followup_turns: maxTurns,
    module_title: path.parse(modulePath!).name,
    actions: [],
    vertical_smoke_passed: false,
    ac_long_passed: false
  };
  const playerDriverModel = configuredPlayerDriverModel();
  const playerDrivers = campaign.players.map(
    () => new AdaptivePlayerDriver(playerDriverModel)
  );
  if (sourceCommit) evidence.source_commit = sourceCommit;

  try {
    if (!useSavedModel) {
      await configureRealCaseModel(campaign.kpPage, {
        baseUrl: modelBaseUrl!,
        apiKey: modelApiKey ?? "",
        modelId: modelId!
      });
    }
    const operatorTitles = await importAndStartFullAiModule(
      campaign.kpPage,
      modulePath!,
      reportStage
    );

    const parallelTraces = await playParallelPublishedOperatorCohort(
      campaign.players,
      operatorTitles,
      0,
      reportStage
    );
    (evidence.actions as unknown[]).push(...parallelTraces);

    expect(parallelTraces).toHaveLength(4);
    for (const trace of parallelTraces) {
      expect(
        trace.confirmation_count,
        `${trace.persona} must confirm its own proposed ruling in the real player UI`
      ).toBeGreaterThan(0);
    }
    expect(
      parallelTraces.reduce((count, trace) => count + trace.digital_roll_count, 0),
      "the four-player vertical must exercise at least one player-owned digital roll"
    ).toBeGreaterThan(0);

    const endingCard = campaign.players[0].page.getByLabel("本次故事已结束");
    if (await endingCard.isVisible().catch(() => false)) {
      await endingCard.getByText("查看关键选择与完整回放").click({ timeout: 30_000 });
    }
    const authoritativeTurns = campaign.players[0].page
      .locator(".public-turn-card:not(.story-opening-card)");
    await expect(
      authoritativeTurns.first(),
      "a confirmed cohort must produce a public authoritative turn, not narration-only progress"
    ).toBeVisible({ timeout: 30_000 });
    evidence.authoritative_settlement_observed = true;
    evidence.public_turn_count = await authoritativeTurns.count();

    let endingReached = parallelTraces.some((trace) => trace.result === "story_completed");
    let followupTurns = 0;
    while (!endingReached && followupTurns < maxTurns) {
      const playerIndex = followupTurns % campaign.players.length;
      const persona = REAL_PLAYER_PERSONAS[followupTurns % REAL_PLAYER_PERSONAS.length];
      reportStage(`natural follow-up ${followupTurns + 1}/${maxTurns} · ${persona.id}`);
      const decision = await playerDrivers[playerIndex].nextAction(
        campaign.players[playerIndex].page,
        persona
      );
      const trace = await playPlayerTurn(
        campaign.players[playerIndex].page,
        persona,
        followupTurns + 1,
        { actionOverride: decision.action }
      );
      (evidence.actions as unknown[]).push({
        persona: persona.id,
        input: decision.action,
        driver_source: decision.source,
        driver_approach: decision.approach,
        driver_recovery_reason: decision.recoveryReason,
        observation_fingerprint: decision.observationFingerprint,
        ...trace,
        parallel: false
      });
      followupTurns += 1;
      endingReached = trace.result === "story_completed";
    }

    evidence.followup_turns_played = followupTurns;
    evidence.ending_reached = endingReached;
    evidence.session_end_completed = false;
    if (endingReached) {
      await endFullAiSession(campaign.kpPage);
      evidence.session_end_completed = true;
      evidence.qualification = "vertical_smoke_with_ending";
    } else {
      evidence.qualification = "vertical_smoke_only";
    }
    evidence.completed_at = new Date().toISOString();
    evidence.vertical_smoke_passed = true;
  } finally {
    evidence.authority_refs = await authorityRefs.snapshot();
    if (evidencePath) {
      await mkdir(path.dirname(evidencePath), { recursive: true });
      await writeFile(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
    }
    await closeFullAiCampaign(campaign);
  }
});
