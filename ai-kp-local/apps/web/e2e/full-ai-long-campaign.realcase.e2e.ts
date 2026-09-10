import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { expect, test } from "@playwright/test";
import {
  addPlayerToFullAiCampaign,
  closeFullAiCampaign,
  createFullAiCampaign
} from "./support/fullAiCampaign";
import { AuthorityRefCollector } from "./support/authorityEvidence";
import { ManagedBackendProcess } from "./support/backendProcess";
import { buildProcessRestartEvidence } from "./support/restartEvidence";
import { UiCorroborationCollector } from "../src/evaluation/uiJourneyCorroboration";
import {
  AdaptivePlayerDriver,
  configuredPlayerDriverModel
} from "./support/adaptivePlayerDriver";
import {
  configureRealCaseModel,
  continueFullAiCampaign,
  endFullAiSession,
  importAndStartFullAiModule,
  playParallelPublishedOperatorCohort
} from "./support/fullAiRealCaseJourney";
import {
  playPlayerTurn,
  REAL_PLAYER_PERSONAS
} from "./support/playerAgent";
import {
  killAndReplaceInvestigator,
  movePlayerToObserver,
  prepareReplacementInvestigator,
  temporarilyLeaveAndReturn
} from "./support/longCampaignLifecycle";
import { playRulesetCombatFromUi } from "./support/longCampaignCombat";
import { registerAndPickUpLootFromUi } from "./support/longCampaignInventory";
import { applyFirstAvailableGrowthFromUi } from "./support/longCampaignGrowth";
import { exerciseAiAssistedGmHandoff } from "./support/longCampaignGm";
import { observePlayerStandardBaseline } from "./support/longCampaignRps";

function configuredModulePaths(): string[] {
  const encoded = process.env.AI_KP_REALCASE_MODULE_PATHS_JSON;
  let paths: string[];
  if (encoded) {
    const parsed: unknown = JSON.parse(encoded);
    if (!Array.isArray(parsed) || parsed.some((item) => typeof item !== "string" || !item.trim())) {
      throw new Error("AI_KP_REALCASE_MODULE_PATHS_JSON must be a JSON array of paths");
    }
    paths = parsed.map((item) => path.resolve(item.trim()));
  } else {
    const single = process.env.AI_KP_REALCASE_MODULE_PATH;
    paths = single ? [path.resolve(single)] : [];
  }
  if (new Set(paths).size !== paths.length) {
    throw new Error("AC-LONG module paths must be distinct; repeating one module is not evidence");
  }
  return paths;
}

const modulePaths = configuredModulePaths();
const modelBaseUrl = process.env.AI_KP_REALCASE_BASE_URL;
const modelApiKey = process.env.AI_KP_REALCASE_API_KEY;
const modelId = process.env.AI_KP_REALCASE_MODEL;
const useSavedModel = process.env.AI_KP_REALCASE_USE_SAVED_MODEL === "1";
const playerDriverModel = configuredPlayerDriverModel();
const evidencePath = process.env.AI_KP_REALCASE_EVIDENCE_PATH;
const sourceCommit = process.env.AI_KP_REALCASE_SOURCE_COMMIT;
if (sourceCommit && !/^[0-9a-f]{7,40}$/.test(sourceCommit)) {
  throw new Error("AI_KP_REALCASE_SOURCE_COMMIT must be a 7..40 character lowercase Git hash");
}
const sessionCount = Number(process.env.AI_KP_REALCASE_SESSIONS ?? "10");
if (!Number.isInteger(sessionCount) || sessionCount < 10) {
  throw new Error("AI_KP_REALCASE_SESSIONS must be an integer of at least 10");
}
const managedBackend = process.env.AI_KP_PLAYWRIGHT_MANAGED_BACKEND === "1";
const backendPort = Number(process.env.AI_KP_PLAYWRIGHT_BACKEND_PORT ?? "8012");
const frontendPort = Number(process.env.AI_KP_PLAYWRIGHT_FRONTEND_PORT ?? "5174");
const databasePath = process.env.AI_KP_PLAYWRIGHT_DB_PATH;
if (managedBackend && (!databasePath || !path.isAbsolute(databasePath))) {
  throw new Error("Managed AC-LONG requires an absolute AI_KP_PLAYWRIGHT_DB_PATH");
}
if (!Number.isInteger(backendPort) || backendPort < 1 || backendPort > 65_535) {
  throw new Error("AI_KP_PLAYWRIGHT_BACKEND_PORT must be a valid TCP port");
}
if (!Number.isInteger(frontendPort) || frontendPort < 1 || frontendPort > 65_535) {
  throw new Error("AI_KP_PLAYWRIGHT_FRONTEND_PORT must be a valid TCP port");
}

test.skip(
  !managedBackend
    || modulePaths.length === 0
    || (!useSavedModel && (!modelBaseUrl || !modelId))
    || !playerDriverModel
    || !sourceCommit,
  "Set MANAGED_BACKEND=1, MODULE_PATH(S_JSON), SOURCE_COMMIT, a saved or explicit KP model, and an explicit player-driver model; API keys are optional for local services"
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
  // Long real-model runs can spend minutes in one bounded background phase.
  // Keep a terse external heartbeat so a stalled UI wait is distinguishable
  // from active model work without inspecting private prompts or credentials.
  console.log(`[AC-LONG] ${stage}`);
}

test("four real player UIs complete a long Full AI campaign through Session End and Continue", async ({
  browser
}) => {
  const manager = new ManagedBackendProcess({
    port: backendPort,
    databasePath: databasePath!,
    frontendUrl: `http://127.0.0.1:${frontendPort}`
  });
  backendManager = manager;
  await manager.start();
  const startedAt = new Date().toISOString();
  const authorityRefs = new AuthorityRefCollector();
  const uiCorroboration = new UiCorroborationCollector();
  const campaign = await createFullAiCampaign(browser, {
    campaignTitle: `AC-LONG Full AI ${Date.now().toString(36)}`,
    playerCount: 4,
    worldview: "长期调查；允许自由发散、角色扮演与幽默，但危险、检定和永久改变必须由玩家确认",
    authorityRefs,
    authorizeKpPage: (page) => manager.authorizeAdministratorPage(page),
    reportStage
  });
  const evidence: Record<string, unknown> = {
    evidence_kind: "long_campaign_ui_journey",
    started_at: startedAt,
    mode: "ai_kp",
    semantic_profile: "small",
    player_count: campaign.players.length,
    player_personas: REAL_PLAYER_PERSONAS.map((persona) => persona.id),
    combat_ui_events: [],
    improvised_ui_events: [],
    inventory_ui_events: [],
    growth_ui_events: [],
    gm_ui_events: [{ kind: "new_gm_guided_setup", session_index: 0 }],
    player_standard_ui_events: [],
    lifecycle_ui_events: [],
    sessions: []
  };
  if (sourceCommit) evidence.source_commit = sourceCommit;
  const playerDrivers = campaign.players.map(
    () => new AdaptivePlayerDriver(playerDriverModel)
  );
  const activePlayerIndexes = new Set(campaign.players.map((_, index) => index));
  const replacementName = `后备调查员-${Date.now().toString(36)}`;

  try {
    if (!useSavedModel) {
      await configureRealCaseModel(campaign.kpPage, {
        baseUrl: modelBaseUrl!,
        apiKey: modelApiKey ?? "",
        modelId: modelId!
      });
    }
    reportStage("campaign setup: preparing an approved replacement investigator");
    await prepareReplacementInvestigator(campaign, campaign.players[0], replacementName);
    let moduleIndex = 0;
    let moduleOperatorTitles = await importAndStartFullAiModule(
      campaign.kpPage,
      modulePaths[moduleIndex],
      reportStage
    );
    let completedModuleCount = 0;
    let observedPublicTurnCount = 0;

    for (let sessionIndex = 0; sessionIndex < sessionCount; sessionIndex += 1) {
      reportStage(`session ${sessionIndex + 1}: opening player workspaces`);
      if (sessionIndex === 2) {
        reportStage("session 3: AI-assisted GM takeover, correction, and handback");
        await exerciseAiAssistedGmHandoff(
          campaign,
          Date.now().toString(36)
        );
        (evidence.gm_ui_events as unknown[]).push({
          kind: "ai_assisted_gm_handoff",
          session_index: sessionIndex + 1
        });
      }
      if (sessionIndex === 3) {
        reportStage("session 4: ruleset-owned combat through KP and player UI");
        await playRulesetCombatFromUi(
          campaign,
          campaign.players[2],
          `长期 Campaign 冲突-${Date.now().toString(36)}`,
          { improvisedAgent: true }
        );
        (evidence.combat_ui_events as unknown[]).push({
          kind: "ruleset_combat_completed",
          session_index: sessionIndex + 1
        });
        (evidence.improvised_ui_events as unknown[]).push({
          kind: "improvised_agent_action_confirmed",
          session_index: sessionIndex + 1
        });
        reportStage("session 4: KP records public loot and a player picks it up");
        await registerAndPickUpLootFromUi(
          campaign,
          campaign.players[2],
          `冲突遗留物-${Date.now().toString(36)}`
        );
        (evidence.inventory_ui_events as unknown[]).push({
          kind: "loot_registered_and_picked_up",
          session_index: sessionIndex + 1
        });
        (evidence.gm_ui_events as unknown[]).push({
          kind: "veteran_gm_authority_tools",
          session_index: sessionIndex + 1
        });
      }
      if (sessionIndex === 4) {
        reportStage("session 5: ruleset death and player-confirmed replacement");
        await killAndReplaceInvestigator(
          campaign,
          campaign.players[0],
          replacementName
        );
        (evidence.lifecycle_ui_events as unknown[] | undefined)?.push({
          kind: "death_and_replacement",
          session_index: sessionIndex + 1
        });
      }
      if (sessionIndex === 5) {
        reportStage("session 6: temporary leave and return");
        await temporarilyLeaveAndReturn(campaign, campaign.players[1]);
        (evidence.lifecycle_ui_events as unknown[] | undefined)?.push({
          kind: "temporary_leave_and_return",
          session_index: sessionIndex + 1
        });
      }
      if (sessionIndex === 6) {
        reportStage("session 7: late player joins through a one-seat invitation");
        await addPlayerToFullAiCampaign(browser, campaign, {
          authorityRefs,
          reportStage
        });
        playerDrivers.push(new AdaptivePlayerDriver(playerDriverModel));
        activePlayerIndexes.add(campaign.players.length - 1);
        (evidence.lifecycle_ui_events as unknown[] | undefined)?.push({
          kind: "late_player_joined",
          session_index: sessionIndex + 1
        });
      }
      if (sessionIndex === 7) {
        reportStage("session 8: one original player leaves and becomes Observer");
        await movePlayerToObserver(campaign, campaign.players[3]);
        activePlayerIndexes.delete(3);
        (evidence.lifecycle_ui_events as unknown[] | undefined)?.push({
          kind: "player_departed_to_observer",
          session_index: sessionIndex + 1
        });
      }
      if (sessionIndex === 8) {
        reportStage("session 9: ruleset growth and player-confirmed permanent change");
        const grownInvestigator = await applyFirstAvailableGrowthFromUi(
          campaign,
          [...activePlayerIndexes].map((index) => campaign.players[index])
        );
        (evidence.growth_ui_events as unknown[]).push({
          kind: "ruleset_growth_player_confirmed",
          session_index: sessionIndex + 1,
          investigator_name: grownInvestigator
        });
      }
      let completed = false;
      const sessionEvidence: Record<string, unknown> = {
        index: sessionIndex + 1,
        module_title: path.parse(modulePaths[moduleIndex]).name,
        actions: []
      };
      const playOne = async (playerIndex: number) => {
        const player = campaign.players[playerIndex];
        const personaIndex = (sessionIndex * campaign.players.length + playerIndex)
          % REAL_PLAYER_PERSONAS.length;
        const persona = REAL_PLAYER_PERSONAS[personaIndex];
        const decision = await playerDrivers[playerIndex].nextAction(player.page, persona);
        const trace = await playPlayerTurn(
          player.page,
          persona,
          sessionIndex,
          { actionOverride: decision.action }
        );
        (sessionEvidence.actions as unknown[]).push({
          persona: persona.id,
          input: decision.action,
          driver_source: decision.source,
          driver_approach: decision.approach,
          driver_recovery_reason: decision.recoveryReason,
          observation_fingerprint: decision.observationFingerprint,
          ...trace
        });
        return trace.result;
      };

      for (const player of campaign.players) await player.page.goto("/play");
      if (sessionIndex === 0) {
        // The first scene deliberately exercises the product's durable 2–12
        // player cohort and atomic settlement rather than a synthetic fixture.
        // Submit through four real browser contexts inside the fixed cohort
        // window.  A small stagger avoids an artificial same-millisecond SQLite
        // write collision without stretching the group into a sliding window.
        reportStage("session 1: submitting four-player cohort");
        // A valid small module may expose fewer operators than seats. Multiple
        // investigators choosing the same published action is a real tabletop
        // case (for example, jointly searching one room), and the parallel
        // kernel must still preserve one consent/outcome per actor.
        const traces = await playParallelPublishedOperatorCohort(
          campaign.players,
          moduleOperatorTitles,
          sessionIndex,
          (stage) => reportStage(`session 1: ${stage}`)
        );
        (sessionEvidence.actions as unknown[]).push(...traces);
        completed = traces.some((trace) => trace.result === "story_completed");
      } else {
        for (const playerIndex of activePlayerIndexes) {
          if (await playOne(playerIndex) === "story_completed") {
            completed = true;
            break;
          }
        }
      }
      const publicTurns = await campaign.players[0].page
        .locator(".public-turn-card")
        .allTextContents();
      sessionEvidence.public_turns = publicTurns.slice(observedPublicTurnCount);
      observedPublicTurnCount = publicTurns.length;
      (evidence.sessions as unknown[]).push(sessionEvidence);
      if (sessionIndex === 0) {
        evidence.player_standard_ui_events = await observePlayerStandardBaseline(
          campaign.players[0].page,
          sessionIndex + 1
        );
      }
      if (completed) {
        const endingCard = campaign.players[0].page.getByLabel("本次故事已结束");
        await expect(endingCard).toBeVisible({ timeout: 30_000 });
        uiCorroboration.record("authoritative_ending_visible", sessionIndex + 1);
        completedModuleCount += 1;
        if (!("single_session_anchor_evidence" in evidence)) {
          // Capture the first completed run before another module can enter the
          // collector. The read-only verifier later reconstructs the actual
          // ending and gameplay branches from these endpoint-scoped IDs.
          evidence.single_session_anchor_evidence = {
            started_at: startedAt,
            completed_at: new Date().toISOString(),
            mode: "ai_kp",
            source_commit: sourceCommit,
            authority_refs: await authorityRefs.snapshot()
          };
        }
      }
      await endFullAiSession(
        campaign.kpPage,
        (kind) => uiCorroboration.record(kind, sessionIndex + 1)
      );
      if (sessionIndex + 1 < sessionCount) {
        if (sessionIndex === 0) {
          const checkpoint = await authorityRefs.latestContinuity();
          if (!checkpoint) {
            throw new Error("Session End did not expose a continuity checkpoint authority ref");
          }
          const before = new Date().toISOString();
          const restart = await manager.restart();
          await Promise.all([
            campaign.kpPage.goto("/play"),
            ...campaign.players.map((player) => player.page.goto("/play"))
          ]);
          await expect(campaign.kpPage.getByRole("heading", { name: /Previously on/ }))
            .toBeVisible({ timeout: 30_000 });
          await continueFullAiCampaign(
            campaign.kpPage,
            (kind) => uiCorroboration.record(kind, sessionIndex + 1)
          );
          evidence.process_restart = buildProcessRestartEvidence({
            restart,
            checkpointSnapshotId: checkpoint.snapshotId,
            observedAt: { before, after: new Date().toISOString() }
          });
        } else {
          await continueFullAiCampaign(
            campaign.kpPage,
            (kind) => uiCorroboration.record(kind, sessionIndex + 1)
          );
        }
      }
      if (completed && sessionIndex + 1 < sessionCount) {
        moduleIndex += 1;
        if (moduleIndex >= modulePaths.length) {
          throw new Error(
            "The Campaign reached a module ending before ten Sessions; provide another generic module in AI_KP_REALCASE_MODULE_PATHS_JSON"
          );
        }
        moduleOperatorTitles = await importAndStartFullAiModule(
          campaign.kpPage,
          modulePaths[moduleIndex],
          reportStage
        );
      }
    }

    expect(
      (evidence.sessions as unknown[]).length,
      "a short ending does not satisfy the ten-Session AC-LONG journey"
    ).toBe(sessionCount);
    expect(
      completedModuleCount,
      "at least one published ScenarioContract must reach a real ending from player UI"
    ).toBeGreaterThan(0);
    evidence.completed_at = new Date().toISOString();
    evidence.completed_module_count = completedModuleCount;
    evidence.scripted_sessions = sessionCount;
  } finally {
    evidence.authority_refs = await authorityRefs.snapshot();
    evidence.ui_corroboration_events = uiCorroboration.snapshot();
    if (evidencePath) {
      await mkdir(path.dirname(evidencePath), { recursive: true });
      await writeFile(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
    }
    await closeFullAiCampaign(campaign);
  }
});
