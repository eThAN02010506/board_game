import path from "node:path";
import { expect, type Page } from "@playwright/test";
import type { UiCorroborationKind } from "../../src/evaluation/uiJourneyCorroboration";
import type { FullAiPlayer } from "./fullAiCampaign";
import {
  REAL_PLAYER_PERSONAS,
  playerActionSurfaceDiagnostics,
  settleSubmittedPlayerAction,
  submitPlayerAction,
  type PlayerTurnTrace
} from "./playerAgent";

export type RealCaseModelConfig = {
  baseUrl: string;
  apiKey: string;
  modelId: string;
};

export type ParallelCohortAction = PlayerTurnTrace & {
  persona: string;
  input: string;
  parallel: true;
};

type StageReporter = (stage: string) => void;

async function waitForPendingParallelProjection(page: Page, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const response = await page.waitForResponse((candidate) =>
      candidate.request().method() === "GET"
      && /\/campaigns\/[^/]+\/parallel-action-batches\/current$/.test(
        new URL(candidate.url()).pathname
      ), { timeout: Math.max(1, deadline - Date.now()) });
    if (!response.ok()) continue;
    const payload = await response.json().catch(() => null) as {
      id?: unknown;
      self_phase?: unknown;
      own_item?: { adjudication?: { status?: unknown } };
    } | null;
    if (payload?.own_item?.adjudication?.status === "pending") {
      return {
        id: typeof payload.id === "string" ? payload.id : "",
        selfPhase: typeof payload.self_phase === "string" ? payload.self_phase : ""
      };
    }
  }
  throw new Error("Player UI did not receive a pending parallel adjudication projection");
}

function realCaseActionTimeoutMs(): number {
  const value = Number(process.env.AI_KP_REALCASE_ACTION_TIMEOUT_MS ?? "360000");
  if (!Number.isInteger(value) || value < 10_000 || value > 900_000) {
    throw new Error("AI_KP_REALCASE_ACTION_TIMEOUT_MS must be 10000..900000");
  }
  return value;
}

async function settleWithHardDeadline(
  player: FullAiPlayer,
  playerIndex: number,
  turnIndex: number,
  action: string
): Promise<PlayerTurnTrace> {
  const timeoutMs = realCaseActionTimeoutMs();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const hardDeadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      void playerActionSurfaceDiagnostics(player.page).then((diagnostics) => {
        reject(new Error(
          `${REAL_PLAYER_PERSONAS[playerIndex % REAL_PLAYER_PERSONAS.length].id} hard UI deadline: ${JSON.stringify(diagnostics)}`
        ));
      }, reject);
    }, timeoutMs + 5_000);
  });
  try {
    return await Promise.race([
      settleSubmittedPlayerAction(
        player.page,
        REAL_PLAYER_PERSONAS[playerIndex % REAL_PLAYER_PERSONAS.length],
        turnIndex,
        { actionOverride: action, timeoutMs }
      ),
      hardDeadline
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export async function configureRealCaseModel(
  page: Page,
  config: RealCaseModelConfig
) {
  await page.goto("/models");
  const textModelSettings = page.locator(".model-settings-main");
  await textModelSettings.getByRole("button", { name: "服务地址" }).click();
  await textModelSettings.getByLabel("OpenAI-compatible 地址").fill(config.baseUrl);
  await textModelSettings.getByLabel("API Key").fill(config.apiKey);
  await textModelSettings.getByLabel("模型 ID").fill(config.modelId);
  await textModelSettings.getByLabel("语义能力档位").selectOption("small");
  await textModelSettings.getByRole("button", { name: "保存并使用" }).click();
  await expect(textModelSettings.getByText(/已保存|正在使用|配置/).last()).toBeVisible({
    timeout: 30_000
  });
}

export async function importAndStartFullAiModule(
  page: Page,
  modulePath: string,
  reportStage: StageReporter = () => undefined
): Promise<string[]> {
  await page.goto("/modules");
  const moduleTitle = path.parse(modulePath).name;
  reportStage(`uploading ${path.basename(modulePath)}`);
  await page.getByLabel("选择 PDF、DOC 或 DOCX").setInputFiles(modulePath);
  const job = page.locator(".module-job").filter({ hasText: path.basename(modulePath) }).first();
  await expect(job).toContainText("导入完成", { timeout: 20 * 60 * 1000 });
  reportStage(`imported ${path.basename(modulePath)}`);
  const moduleRecord = page.locator(".record-button").filter({ hasText: moduleTitle }).first();
  await expect(moduleRecord).toBeVisible();
  await moduleRecord.click();
  await page.getByLabel("当前场景").fill("opening");
  await page.getByRole("button", { name: `开始《${moduleTitle}》` }).click();
  await expect(page.getByText("活动模组", { exact: true })).toBeVisible();

  const automation = page.getByRole("region", { name: "自动化强度" });
  const fullAi = automation.getByRole("button", { name: "AI KP" });
  await expect(fullAi).toBeEnabled({ timeout: 30_000 });
  await fullAi.click();
  await expect(automation).toContainText("当前：AI KP", { timeout: 30_000 });

  const scope = page.getByLabel("本次要玩的剧本范围");
  if (await scope.isVisible().catch(() => false)) {
    const options = scope.locator("option:not([value=''])");
    await expect(options.first()).toBeAttached();
    await scope.selectOption(await options.first().getAttribute("value") ?? "");
  }

  const kernelEnabled = page.getByText("Kernel 已启用");
  const contractJobs = page.getByLabel("契约后台任务");
  let contractJobText = "";
  for (let semanticAttempt = 1; semanticAttempt <= 3; semanticAttempt += 1) {
    const generate = page.getByRole("button", { name: "从模组证据生成" });
    await expect(generate).toBeEnabled({ timeout: 30_000 });
    await generate.click();
    reportStage(
      `authoring ${path.basename(modulePath)} · semantic attempt ${semanticAttempt}/3`
    );
    await expect(generate).toBeDisabled({ timeout: 30_000 });
    await expect.poll(async () => {
      if (await kernelEnabled.isVisible().catch(() => false)) return "published-and-bound";
      if (await generate.isEnabled().catch(() => false)) return "terminal-unbound";
      return "running";
    }, {
      timeout: 90 * 60 * 1000,
      intervals: [1_000, 2_000, 5_000]
    }).toMatch(/^(published-and-bound|terminal-unbound)$/);
    if (await kernelEnabled.isVisible().catch(() => false)) {
      contractJobText = "完成 · 已自动审核发布";
      break;
    }
    const latestJob = contractJobs.locator("article").first();
    await expect(latestJob).toBeVisible();
    const latestJobStatus = latestJob.locator("strong").first();
    await expect(latestJobStatus).toHaveText(
      /^(完成 · 已自动审核发布|完成 · 自动审核未通过|完成 · 未形成有效契约|完成 · 草稿待人工接管|失败 · 后台契约编译失败)$/
    );
    contractJobText = await latestJob.innerText();
    if (contractJobText.includes("完成 · 已自动审核发布")) break;
  }
  if (!contractJobText.includes("完成 · 已自动审核发布")) {
    throw new Error(
      `Full-AI contract publication stopped fail-closed after three semantic attempts:\n${contractJobText}`
    );
  }
  reportStage(`published ${path.basename(modulePath)}`);
  const bind = page.getByRole("button", { name: "绑定已发布版本" });
  if (!(await kernelEnabled.isVisible().catch(() => false))) {
    await expect(bind).toBeEnabled({ timeout: 30_000 });
    await bind.click();
  }
  await expect(kernelEnabled).toBeVisible({ timeout: 30_000 });
  reportStage(`bound ${path.basename(modulePath)}`);

  const publishedVersion = page.locator(".scenario-contract-versions article")
    .filter({ hasText: "published" })
    .first();
  const contractText = await publishedVersion.locator(".scenario-contract-json pre").textContent();
  const contract = JSON.parse(contractText ?? "{}") as {
    operators?: Array<{ title?: string }>;
  };
  const operatorTitles = (contract.operators ?? [])
    .map((operator) => operator.title?.trim() ?? "")
    .filter(Boolean);
  if (operatorTitles.length === 0) {
    throw new Error(
      "Published contract must expose at least one executable operator for the parallel UI proof"
    );
  }
  return operatorTitles;
}

export async function playParallelPublishedOperatorCohort(
  players: readonly FullAiPlayer[],
  operatorTitles: readonly string[],
  turnIndex: number,
  reportStage: StageReporter = () => undefined
): Promise<ParallelCohortAction[]> {
  if (players.length !== 4) {
    throw new Error(`Four-player real-case cohort requires exactly 4 player UIs; received ${players.length}`);
  }
  if (operatorTitles.length === 0) {
    throw new Error("Parallel cohort requires at least one published operator");
  }
  for (const player of players) await player.page.goto("/play");
  const actions = players.map((_, playerIndex) =>
    `选择已发布行动“${operatorTitles[playerIndex % operatorTitles.length]}”。`
  );
  const projectionWaiters = players.map((player) =>
    waitForPendingParallelProjection(player.page)
  );
  reportStage("submitting four-player cohort");
  await Promise.all(players.map(async (player, playerIndex) => {
    await new Promise((resolve) => setTimeout(resolve, playerIndex * 200));
    await submitPlayerAction(
      player.page,
      REAL_PLAYER_PERSONAS[playerIndex % REAL_PLAYER_PERSONAS.length],
      turnIndex,
      actions[playerIndex]
    );
  }));
  const projections = await Promise.all(projectionWaiters);
  if (new Set(projections.map((item) => item.id)).size !== 1) {
    throw new Error(`Player UIs observed different parallel batches: ${JSON.stringify(projections)}`);
  }
  console.log(`[VERTICAL-SMOKE] all player projections pending · ${projections[0].id}`);
  reportStage("settling four-player cohort");
  const traces = await Promise.all(players.map((player, playerIndex) =>
    settleWithHardDeadline(player, playerIndex, turnIndex, actions[playerIndex])
  ));
  return traces.map((trace, playerIndex) => ({
    persona: REAL_PLAYER_PERSONAS[playerIndex % REAL_PLAYER_PERSONAS.length].id,
    input: actions[playerIndex],
    ...trace,
    parallel: true
  }));
}

export async function endFullAiSession(
  page: Page,
  observe?: (kind: UiCorroborationKind) => void
) {
  await page.goto("/play");
  await page.getByRole("button", { name: "Session End" }).click();
  await expect(page.getByRole("heading", { name: /Previously on/ })).toBeVisible({
    timeout: 30_000
  });
  observe?.("session_end_visible");
}

export async function continueFullAiCampaign(
  page: Page,
  observe?: (kind: UiCorroborationKind) => void
) {
  await page.getByRole("button", { name: "Continue Campaign" }).click();
  await expect(page.getByText(/第 \d+ 次 Session/).first()).toBeVisible({
    timeout: 30_000
  });
  observe?.("continue_campaign_visible");
}
