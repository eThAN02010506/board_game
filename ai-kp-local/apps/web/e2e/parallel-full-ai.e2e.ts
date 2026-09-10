import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

import {
  expect,
  test,
  type APIRequestContext,
  type BrowserContext,
  type Page
} from "@playwright/test";

test.setTimeout(90_000);

const projectRoot = fileURLToPath(new URL("../../..", import.meta.url));
const fixtureScript = path.join(
  projectRoot,
  "apps/web/e2e/parallel_full_ai_fixture.py"
);
const databasePath = process.env.AI_KP_PLAYWRIGHT_DB_PATH
  ?? path.join(projectRoot, ".playwright/ai-kp.sqlite3");

type Headers = Record<string, string>;

type SessionBundle = {
  access_token: string;
  join_code: string;
  member: { id: string };
  session: { id: string };
};

type PlayerBundle = {
  accessToken: string;
  headers: Headers;
  memberId: string;
  playerToken: string;
};

async function postJson<T>(
  request: APIRequestContext,
  apiPath: string,
  data: unknown,
  headers: Headers = {}
): Promise<T> {
  const response = await request.post(`/api${apiPath}`, { data, headers });
  expect(response.ok(), `${apiPath}: ${await response.text()}`).toBeTruthy();
  return response.json() as Promise<T>;
}

async function getJson<T>(
  request: APIRequestContext,
  apiPath: string,
  headers: Headers = {}
): Promise<T> {
  const response = await request.get(`/api${apiPath}`, { headers });
  expect(response.ok(), `${apiPath}: ${await response.text()}`).toBeTruthy();
  return response.json() as Promise<T>;
}

function investigatorSheet(
  name: string,
  skillKey: string,
  skillName: string,
  target: number
) {
  return {
    schema_version: "coc7-investigator-v1",
    ruleset_id: "coc7-keeper-cn-2002c",
    identity: { name, occupation: "调查员", age: 30, era: "1920s" },
    characteristics: {
      str: 50,
      con: 50,
      siz: 50,
      dex: 50,
      app: 50,
      int: 50,
      pow: 50,
      edu: 50,
      luck: 50
    },
    skills: [{
      skill_key: skillKey,
      display_name: skillName,
      base_value: target,
      occupation_points: 0,
      interest_points: 0,
      development_points: 0
    }],
    assets: { items: [] },
    background: {},
    provenance: { source_type: "playwright-e2e" }
  };
}

async function createPlayer(
  request: APIRequestContext,
  campaignId: string,
  session: SessionBundle,
  kpHeaders: Headers,
  displayName: string,
  sheet: ReturnType<typeof investigatorSheet>
): Promise<PlayerBundle> {
  const profile = await postJson<{ player_token: string }>(
    request,
    "/player-profiles",
    { display_name: displayName }
  );
  const joined = await postJson<{
    access_token: string;
    member: { id: string };
  }>(request, "/sessions/join", {
    join_code: session.join_code,
    display_name: displayName
  });
  const headers = {
    Authorization: `Bearer ${joined.access_token}`,
    "X-AI-KP-Player-Token": profile.player_token
  };
  const investigator = await postJson<{
    id: string;
    current_revision_id: string;
  }>(request, "/investigators", {
    canonical_sheet: sheet,
    source_type: "manual"
  }, headers);
  await postJson(
    request,
    `/campaigns/${campaignId}/investigators/${investigator.id}/submit`,
    { revision_id: investigator.current_revision_id },
    headers
  );
  await postJson(
    request,
    `/campaigns/${campaignId}/investigators/${investigator.id}/review`,
    { action: "approved", comment: "双玩家浏览器验收" },
    kpHeaders
  );
  await postJson(
    request,
    `/sessions/${session.session.id}/members/${joined.member.id}/assign-investigator`,
    { investigator_id: investigator.id },
    kpHeaders
  );
  return {
    accessToken: joined.access_token,
    headers,
    memberId: joined.member.id,
    playerToken: profile.player_token
  };
}

function runFixture(
  mode: "install" | "prepare",
  options: Record<string, string>
) {
  const args = [
    fixtureScript,
    mode,
    "--db-path",
    databasePath,
    ...Object.entries(options).flatMap(([key, value]) => [`--${key}`, value])
  ];
  return execFileSync(path.join(projectRoot, ".venv/bin/python"), args, {
    cwd: projectRoot,
    encoding: "utf8",
    env: {
      ...process.env,
      PYTHONPATH: [
        path.join(projectRoot, "src"),
        projectRoot,
        process.env.PYTHONPATH
      ].filter(Boolean).join(path.delimiter)
    }
  }).trim();
}

async function authenticatePlayerContext(
  context: BrowserContext,
  campaignId: string,
  player: PlayerBundle
) {
  await context.addInitScript(
    ({ selectedCampaignId, accessToken, playerToken }) => {
      if (!/^https?:$/.test(window.location.protocol)) return;
      sessionStorage.setItem(
        `ai-kp-session-token:${selectedCampaignId}`,
        accessToken
      );
      localStorage.setItem("ai-kp-player-profile-token", playerToken);
    },
    {
      selectedCampaignId: campaignId,
      accessToken: player.accessToken,
      playerToken: player.playerToken
    }
  );
}

async function expectPlayerDesk(page: Page, investigatorName: string) {
  await expect(page.getByRole("heading", { name: "你的行动桌面" })).toBeVisible();
  await expect(page.getByText(investigatorName, { exact: true }).first()).toBeVisible();
  await expect(page.getByLabel("AI KP 自动推进")).toBeChecked();
}

test("two player UIs consent, roll, wait, and receive one Full AI settlement", async ({
  baseURL,
  browser,
  request
}) => {
  const suffix = `${Date.now().toString(36)}-${test.info().workerIndex}`;
  const checkAction = `我贴近墙面检查那道几乎看不见的暗记。${suffix}`;
  const directAction = `我从另一侧稳稳拉开机关面板。${suffix}`;
  const firstInvestigator = `许闻 ${suffix}`;
  const secondInvestigator = `沈舟 ${suffix}`;

  const campaign = await postJson<{ id: string }>(request, "/campaigns", {
    title: `多人通用契约验收 ${suffix}`,
    system: "coc7",
    current_time: "1928-10-03 20:00"
  });
  const session = await postJson<SessionBundle>(
    request,
    `/campaigns/${campaign.id}/sessions`,
    { kp_display_name: "E2E KP" }
  );
  const kpHeaders = { Authorization: `Bearer ${session.access_token}` };
  const firstPlayer = await createPlayer(
    request,
    campaign.id,
    session,
    kpHeaders,
    `玩家甲 ${suffix}`,
    investigatorSheet(firstInvestigator, "spot_hidden", "侦查", 70)
  );
  const secondPlayer = await createPlayer(
    request,
    campaign.id,
    session,
    kpHeaders,
    `玩家乙 ${suffix}`,
    investigatorSheet(secondInvestigator, "listen", "聆听", 60)
  );
  const sessionZero = await getJson<{
    revision: { id: string; version: number };
  }>(request, `/campaigns/${campaign.id}/session-zero`, kpHeaders);
  for (const headers of [kpHeaders, firstPlayer.headers, secondPlayer.headers]) {
    await postJson(request, `/campaigns/${campaign.id}/session-zero/confirm`, {
      revision_id: sessionZero.revision.id,
      expected_version: sessionZero.revision.version
    }, headers);
  }

  const module = await postJson<{ id: string }>(
    request,
    `/campaigns/${campaign.id}/modules`,
    {
      title: `通用并行场景 ${suffix}`,
      text: "@visibility=kp @spoiler=shared\n两名调查员可以在同一场景同时行动。",
      source_type: "plaintext"
    },
    kpHeaders
  );
  const run = await postJson<{ id: string; version: number }>(
    request,
    `/campaigns/${campaign.id}/module-runs`,
    {
      module_id: module.id,
      current_scene_key: "shared-scene",
      active_spoiler_tags: ["shared"],
      state: {}
    },
    kpHeaders
  );
  runFixture("install", {
    "campaign-id": campaign.id,
    "kp-member-id": session.member.id,
    "module-id": module.id,
    "run-id": run.id
  });
  await postJson(
    request,
    `/module-runs/${run.id}/automation`,
    {
      expected_version: run.version,
      level: "ai_kp",
      reason: "验证 Full AI 多人统一结算"
    },
    kpHeaders
  );

  const firstContext = await browser.newContext({ baseURL, locale: "zh-CN" });
  const secondContext = await browser.newContext({ baseURL, locale: "zh-CN" });
  await authenticatePlayerContext(firstContext, campaign.id, firstPlayer);
  await authenticatePlayerContext(secondContext, campaign.id, secondPlayer);
  const firstPage = await firstContext.newPage();
  const secondPage = await secondContext.newPage();

  try {
    await Promise.all([firstPage.goto("/play"), secondPage.goto("/play")]);
    await Promise.all([
      expectPlayerDesk(firstPage, firstInvestigator),
      expectPlayerDesk(secondPage, secondInvestigator)
    ]);

    await firstPage.getByLabel("玩家行动").fill(checkAction);
    await secondPage.getByLabel("玩家行动").fill(directAction);
    // Keep model interpretation out of this deterministic fixture setup.  The
    // run itself remains Full AI, and the checkbox is restored before consent,
    // rolling, and the automatic atomic commit are exercised.
    await Promise.all([
      firstPage.getByLabel("AI KP 自动推进").uncheck(),
      secondPage.getByLabel("AI KP 自动推进").uncheck()
    ]);
    for (const page of [firstPage, secondPage]) {
      const submission = page.waitForResponse((response) =>
        response.request().method() === "POST"
        && response.url().endsWith(`/api/campaigns/${campaign.id}/actions`)
      );
      await page.getByRole("button", { name: "提交给 KP" }).click();
      const response = await submission;
      expect(response.ok(), await response.text()).toBeTruthy();
    }

    await expect.poll(async () => {
      const actions = await getJson<Array<{ id: string; action_text: string }>>(
        request,
        `/campaigns/${campaign.id}/actions`,
        kpHeaders
      );
      return actions.filter((item) =>
        item.action_text === checkAction || item.action_text === directAction
      ).length;
    }, { timeout: 5_000 }).toBe(2);
    const submitted = await getJson<Array<{ id: string; action_text: string }>>(
      request,
      `/campaigns/${campaign.id}/actions`,
      kpHeaders
    );
    const actionIds = submitted
      .filter((item) => item.action_text === checkAction || item.action_text === directAction)
      .map((item) => item.id);
    runFixture("prepare", {
      "campaign-id": campaign.id,
      "kp-member-id": session.member.id,
      "session-id": session.session.id,
      "action-ids": JSON.stringify(actionIds),
      "check-action-text": checkAction,
      "direct-action-text": directAction,
      "fixture-key": suffix
    });
    await Promise.all([
      firstPage.getByLabel("AI KP 自动推进").check(),
      secondPage.getByLabel("AI KP 自动推进").check()
    ]);

    const firstBatch = firstPage.getByRole("status", { name: "多人并行动作状态" });
    const secondBatch = secondPage.getByRole("status", { name: "多人并行动作状态" });
    await expect(firstBatch).toContainText("等待你确认裁定", { timeout: 12_000 });
    await expect(secondBatch).toContainText("等待你确认裁定", { timeout: 12_000 });
    await expect(firstBatch).toContainText("参与2 人");
    await expect(firstBatch).toContainText("待确认2 人");
    await expect(firstPage.getByRole("button", { name: "修改行动并重新裁定" })).toBeVisible();
    await expect(secondPage.getByRole("button", { name: "修改行动并重新裁定" })).toBeVisible();

    // Before settlement each context receives only its own action/ruling.  The
    // other participant's unrevealed intent must not appear anywhere in the UI.
    await expect(firstPage.locator("body")).not.toContainText(directAction);
    await expect(secondPage.locator("body")).not.toContainText(checkAction);
    await expect(firstPage.getByLabel("选择本次判定技能")).toHaveValue("spot_hidden");
    await expect(secondPage.getByLabel("选择本次判定技能")).toHaveCount(0);

    await firstPage.getByRole("button", { name: "确认此裁定" }).click();
    await expect(firstBatch).toContainText("你的裁定已确认，等待其他玩家");
    await expect(firstBatch).toContainText("已确认1 人");
    await expect(secondBatch).toContainText("等待你确认裁定");
    await expect(secondBatch).toContainText("待确认1 人");
    await expect(firstPage.getByLabel("玩家行动")).toBeDisabled();

    await secondPage.getByRole("button", { name: "确认此裁定" }).click();
    await expect(secondBatch).toContainText("你的步骤已完成，等待其他检定");
    await expect(secondBatch).toContainText("待确认0 人");
    await expect(firstPage.getByRole("tab", { name: "检定" })).toHaveAttribute(
      "aria-selected",
      "true",
      { timeout: 12_000 }
    );

    const ownCheck = firstPage.locator(".check-card").filter({ hasText: "spot_hidden" }).first();
    await expect(ownCheck).toBeVisible();
    await expect(ownCheck).toContainText("待掷骰");
    // The consent barrier is also the visibility boundary: only after every
    // player confirms does the check request become shared table narration.
    await expect(secondPage.locator("body")).toContainText(checkAction);
    await expect(secondPage.locator("body")).toContainText(
      "你辨认墙面上几乎看不见的暗记，结果要等检定后才能确定。"
    );

    await ownCheck.getByText("录入实体骰", { exact: true }).click();
    await ownCheck.getByRole("spinbutton", { name: "个位" }).fill("1");
    await ownCheck.getByRole("textbox", { name: /十位骰/ }).fill("0");
    await ownCheck.getByRole("button", { name: "确认实体骰" }).click();
    await expect(ownCheck.getByRole("button", { name: "重放校验" })).toBeVisible();

    await firstPage.getByRole("tab", { name: "行动" }).click();
    await expect(firstPage.getByLabel("本次故事已结束")).toBeVisible({ timeout: 20_000 });
    await expect(secondPage.getByLabel("本次故事已结束")).toBeVisible({ timeout: 20_000 });

    for (const page of [firstPage, secondPage]) {
      await page.getByText("查看关键选择与完整回放", { exact: true }).click();
      await expect(page.getByText(checkAction, { exact: true }).last()).toBeVisible();
      await expect(page.getByText(directAction, { exact: true }).last()).toBeVisible();
      await expect(page.getByText(
        "暗记的走向被确认，它确实指向机关面板的内部结构。",
        { exact: true }
      )).toBeVisible();
      await expect(page.getByText(
        "机关面板被平稳打开，内部结构完整显露出来。",
        { exact: true }
      )).toBeVisible();
    }

    const jobs = await getJson<Array<{
      job_type: string;
      phase?: string;
      status: string;
    }>>(request, `/campaigns/${campaign.id}/auto-kp/jobs`, firstPlayer.headers);
    const commits = jobs.filter((job) =>
      job.job_type === "parallel_actions" && job.phase === "settlement"
    );
    expect(commits).toHaveLength(1);
    expect(commits[0].status).toBe("succeeded");
  } finally {
    await Promise.all([firstContext.close(), secondContext.close()]);
  }
});
