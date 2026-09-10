import { expect, test, type APIRequestContext, type BrowserContext } from "@playwright/test";

type Headers = Record<string, string>;

async function postJson<T>(
  request: APIRequestContext,
  path: string,
  data: unknown,
  headers: Headers = {}
): Promise<T> {
  const response = await request.post(`/api${path}`, { data, headers });
  expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy();
  return response.json() as Promise<T>;
}

async function putJson<T>(
  request: APIRequestContext,
  path: string,
  data: unknown,
  headers: Headers = {}
): Promise<T> {
  const response = await request.put(`/api${path}`, { data, headers });
  expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy();
  return response.json() as Promise<T>;
}

async function authenticate(context: BrowserContext, campaignId: string, token: string) {
  await context.addInitScript(({ id, accessToken }) => {
    sessionStorage.setItem(`ai-kp-session-token:${id}`, accessToken);
  }, { id: campaignId, accessToken: token });
}

test("Session End freezes role-safe summaries and Continue restores play after reload", async ({
  baseURL,
  browser,
  request
}) => {
  const suffix = Date.now().toString(36);
  const campaign = await postJson<{ id: string; ruleset_id: string; ruleset_version: string }>(
    request,
    "/campaigns",
    { title: `连续性 UI 验收 ${suffix}`, system: "coc7" }
  );
  const session = await postJson<{
    access_token: string;
    join_code: string;
    member: { id: string };
  }>(request, `/campaigns/${campaign.id}/sessions`, { kp_display_name: "连续性 KP" });
  const player = await postJson<{ access_token: string; member: { id: string } }>(
    request,
    "/sessions/join",
    { join_code: session.join_code, display_name: "连续性玩家", role: "player" }
  );
  const observer = await postJson<{ access_token: string; member: { id: string } }>(
    request,
    "/sessions/join",
    { join_code: session.join_code, display_name: "连续性观战", role: "observer" }
  );
  const kpHeaders = { Authorization: `Bearer ${session.access_token}` };
  const playerHeaders = { Authorization: `Bearer ${player.access_token}` };

  const configured = await putJson<{
    revision: { id: string; version: number };
  }>(request, `/campaigns/${campaign.id}/session-zero/config`, {
    expected_version: 1,
    ruleset_id: campaign.ruleset_id,
    ruleset_version: campaign.ruleset_version,
    worldview: "A generic long-running mystery campaign",
    hosting_mode: "ai_kp",
    expected_player_count: 1,
    campaign_type: "ongoing",
    starting_power: "standard",
    allowed_character_options: [],
    house_rules: [],
    default_visibility: "party",
    style: {},
    content_warnings: [],
    lines: [],
    veils: [],
    safety_default: "pause",
    idle_policy: "wait",
    allow_player_whispers: false
  }, kpHeaders);
  await postJson(
    request,
    `/campaigns/${campaign.id}/session-zero/confirm`,
    {
      revision_id: configured.revision.id,
      expected_version: configured.revision.version
    },
    playerHeaders
  );
  await postJson(request, `/campaigns/${campaign.id}/events`, {
    actor_type: "kp",
    event_type: "scene_progress",
    summary: "调查员已经找到钟楼下的公开入口。",
    visibility: "table",
    payload: {}
  }, kpHeaders);
  const knownNpc = await postJson<{ id: string }>(request, `/campaigns/${campaign.id}/npcs`, {
    name: "钟楼管理员",
    profession: "管理员",
    public_notes: "最后见过钟表匠的人。",
    secret_notes: "这个秘密动机不得进入玩家或 Observer 摘要。"
  }, kpHeaders);
  await postJson(request, `/campaigns/${campaign.id}/npcs/${knownNpc.id}`, {
    role: "encountered",
    first_seen_time: "1928-10-03 21:15",
    last_seen_time: "1928-10-03 21:30",
    relationship_score: 0,
    notes: "",
    participant_investigator_ids: []
  }, kpHeaders);
  await postJson(request, `/campaigns/${campaign.id}/inventory/items`, {
    command_id: `continuity-key-${suffix}`,
    item_type: "clue",
    public_name: "钟楼钥匙",
    public_description: "队伍共同保管的关键物品。",
    quantity: 1,
    is_unique: true,
    holder_kind: "party",
    holder_id: campaign.id,
    hidden_properties: { opens: "secret-vault" },
    reason: "连续性 UI 验收"
  }, kpHeaders);
  await postJson(request, `/campaigns/${campaign.id}/events`, {
    actor_type: "kp",
    event_type: "hidden_threat",
    summary: "秘密的追猎者已经醒来。",
    visibility: "kp",
    payload: {}
  }, kpHeaders);

  const kpContext = await browser.newContext({ baseURL, locale: "zh-CN" });
  const playerContext = await browser.newContext({ baseURL, locale: "zh-CN" });
  const observerContext = await browser.newContext({ baseURL, locale: "zh-CN" });
  await authenticate(kpContext, campaign.id, session.access_token);
  await authenticate(playerContext, campaign.id, player.access_token);
  await authenticate(observerContext, campaign.id, observer.access_token);
  const kpPage = await kpContext.newPage();
  const playerPage = await playerContext.newPage();
  const observerPage = await observerContext.newPage();

  try {
    await Promise.all([kpPage.goto("/play"), playerPage.goto("/play"), observerPage.goto("/play")]);
    await expect(playerPage.getByLabel("玩家行动")).toBeEnabled();
    await kpPage.getByLabel("任务标题").fill("查明钟楼停摆原因");
    await kpPage.getByLabel("公开目标").fill("找到仍未解决的机械故障来源。");
    await kpPage.getByRole("button", { name: "建立任务" }).click();
    const objectiveCard = kpPage.locator(".objective-card").filter({ hasText: "查明钟楼停摆原因" });
    await expect(objectiveCard).toBeVisible();
    await objectiveCard.getByLabel("查明钟楼停摆原因进展").fill("入口已找到，但内部机关仍无法启动。");
    await objectiveCard.getByRole("button", { name: "标记受阻" }).click();
    await expect(playerPage.getByText("查明钟楼停摆原因")).toBeVisible({ timeout: 8_000 });
    await kpPage.getByRole("button", { name: "Session End" }).click();

    await expect(kpPage.getByText("Previously on… 调查员已经找到钟楼下的公开入口。", { exact: true })).toBeVisible();
    await kpPage.getByText(/KP 私密回顾/).click();
    await expect(kpPage.getByText("秘密的追猎者已经醒来。")).toBeVisible();
    await expect(playerPage.getByText("调查员已经找到钟楼下的公开入口。")).toBeVisible({ timeout: 8_000 });
    await expect(observerPage.getByText("调查员已经找到钟楼下的公开入口。")).toBeVisible({ timeout: 8_000 });
    await expect(playerPage.getByText("秘密的追猎者已经醒来。")).toHaveCount(0);
    await expect(observerPage.getByText("秘密的追猎者已经醒来。")).toHaveCount(0);
    for (const safePage of [playerPage, observerPage]) {
      await expect(safePage.getByText(/未解决问题：查明钟楼停摆原因/)).toBeVisible();
      await expect(safePage.getByText(/重要 NPC：钟楼管理员/)).toBeVisible();
      await expect(safePage.getByText(/关键物品：钟楼钥匙/)).toBeVisible();
      await expect(safePage.getByText(/secret-vault|秘密动机/)).toHaveCount(0);
    }
    await expect(playerPage.getByLabel("玩家行动")).toBeDisabled();

    await Promise.all([playerPage.reload(), observerPage.reload()]);
    await expect(playerPage.getByText("调查员已经找到钟楼下的公开入口。")).toBeVisible();
    await expect(observerPage.getByText("调查员已经找到钟楼下的公开入口。")).toBeVisible();

    await kpPage.getByRole("button", { name: "Continue Campaign" }).click();
    await expect(kpPage.getByText("第 2 次 Session")).toBeVisible();
    await expect(playerPage.getByLabel("玩家行动")).toBeEnabled({ timeout: 8_000 });
  } finally {
    await Promise.all([kpContext.close(), playerContext.close(), observerContext.close()]);
  }
});
