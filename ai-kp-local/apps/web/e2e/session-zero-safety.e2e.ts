import { expect, test, type APIRequestContext, type BrowserContext } from "@playwright/test";

test.setTimeout(60_000);

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

async function authenticate(
  context: BrowserContext,
  campaignId: string,
  accessToken: string,
  playerToken = ""
) {
  await context.addInitScript(({ id, token, profile }) => {
    sessionStorage.setItem(`ai-kp-session-token:${id}`, token);
    if (profile) localStorage.setItem("ai-kp-player-profile-token", profile);
  }, { id: campaignId, token: accessToken, profile: playerToken });
}

function investigatorSheet(name: string) {
  return {
    schema_version: "coc7-investigator-v1",
    ruleset_id: "coc7-keeper-cn-2002c",
    identity: { name, occupation: "古物研究者", age: 32, era: "1920s" },
    characteristics: {
      str: 50, con: 50, siz: 50, dex: 50, app: 50,
      int: 60, pow: 55, edu: 65, luck: 50
    },
    skills: [],
    assets: { items: [] },
    background: {},
    provenance: { source_type: "session-zero-e2e" }
  };
}

test("KP and player complete Session 0 and use an explanation-free safety pause", async ({
  baseURL,
  browser,
  request
}) => {
  const suffix = Date.now().toString(36);
  const campaign = await postJson<{ id: string }>(request, "/campaigns", {
    title: `Session 0 UI 验收 ${suffix}`,
    system: "coc7"
  });
  const session = await postJson<{
    access_token: string;
    join_code: string;
    member: { id: string };
    session: { id: string };
  }>(request, `/campaigns/${campaign.id}/sessions`, { kp_display_name: "安全工具 KP" });
  const kpHeaders = { Authorization: `Bearer ${session.access_token}` };
  const profile = await postJson<{ player_token: string }>(
    request,
    "/player-profiles",
    { display_name: `边界测试玩家 ${suffix}` }
  );
  const joined = await postJson<{
    access_token: string;
    member: { id: string };
  }>(request, "/sessions/join", {
    join_code: session.join_code,
    display_name: `边界测试玩家 ${suffix}`
  });
  const playerHeaders = {
    Authorization: `Bearer ${joined.access_token}`,
    "X-AI-KP-Player-Token": profile.player_token
  };
  const investigator = await postJson<{ id: string; current_revision_id: string }>(
    request,
    "/investigators",
    { canonical_sheet: investigatorSheet(`顾安 ${suffix}`), source_type: "manual" },
    playerHeaders
  );
  await postJson(
    request,
    `/campaigns/${campaign.id}/investigators/${investigator.id}/submit`,
    { revision_id: investigator.current_revision_id },
    playerHeaders
  );
  await postJson(
    request,
    `/campaigns/${campaign.id}/investigators/${investigator.id}/review`,
    { action: "approved", comment: "Session 0 UI 验收" },
    kpHeaders
  );
  await postJson(
    request,
    `/sessions/${session.session.id}/members/${joined.member.id}/assign-investigator`,
    { investigator_id: investigator.id },
    kpHeaders
  );

  const kpContext = await browser.newContext({ baseURL, locale: "zh-CN" });
  const playerContext = await browser.newContext({ baseURL, locale: "zh-CN" });
  await authenticate(kpContext, campaign.id, session.access_token);
  await authenticate(playerContext, campaign.id, joined.access_token, profile.player_token);
  const kpPage = await kpContext.newPage();
  const playerPage = await playerContext.newPage();

  try {
    await Promise.all([kpPage.goto("/play"), playerPage.goto("/play")]);
    await expect(playerPage.getByText("正式游玩前需要确认")).toBeVisible();
    await expect(playerPage.getByLabel("玩家行动")).toBeDisabled();
    await expect(kpPage.getByText("版本 1", { exact: true })).toBeVisible();

    const campaignSetup = kpPage.locator("details").filter({
      has: kpPage.locator("summary").filter({ hasText: "Campaign 设置与公共边界" })
    });
    if (!await campaignSetup.evaluate((element) => (element as HTMLDetailsElement).open)) {
      await campaignSetup.locator("summary").click();
    }
    await campaignSetup.getByLabel("世界观").fill("长期调查、克制恐怖、允许偶尔幽默");
    await campaignSetup.getByLabel("Lines（绝不出现）").fill("针对玩家本人的羞辱");
    const savedConfig = kpPage.waitForResponse((response) =>
      response.request().method() === "PUT"
      && response.url().includes("/session-zero/config")
    );
    await campaignSetup.getByRole("button", { name: "保存新版本并确认" }).click();
    const savedConfigResponse = await savedConfig;
    expect(savedConfigResponse.ok(), await savedConfigResponse.text()).toBeTruthy();
    await expect(kpPage.getByText("版本 2", { exact: true })).toBeVisible();
    await expect(kpPage.getByText("1/2", { exact: true })).toBeVisible();

    await playerPage.reload();
    await expect(playerPage.getByText("版本 2", { exact: true })).toBeVisible({ timeout: 8_000 });
    await expect(playerPage.getByRole("button", { name: "确认当前 Session 0" })).toBeVisible();
    await playerPage.getByRole("button", { name: "确认当前 Session 0" }).click();
    await expect(playerPage.getByText("全桌已确认")).toBeVisible();
    await expect(playerPage.getByLabel("玩家行动")).toBeEnabled();

    await playerPage.getByRole("button", { name: "暂停", exact: true }).click();
    await expect(playerPage.getByRole("alert")).toBeVisible();
    await expect(kpPage.getByRole("alert")).toBeVisible({ timeout: 8_000 });
    await expect(kpPage.getByRole("alert")).toContainText(
      "安全工具已触发。当前内容暂停，触发者无需说明原因。"
    );
    await expect(kpPage.getByRole("alert")).not.toContainText("因为");
    await kpPage.getByRole("button", { name: "已处理，继续" }).click();
    await expect(playerPage.getByRole("alert")).toHaveCount(0, { timeout: 8_000 });
  } finally {
    await Promise.all([kpContext.close(), playerContext.close()]);
  }
});
