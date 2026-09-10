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

async function authenticate(context: BrowserContext, campaignId: string, token: string) {
  await context.addInitScript(({ id, accessToken }) => {
    sessionStorage.setItem(`ai-kp-session-token:${id}`, accessToken);
  }, { id: campaignId, accessToken: token });
}

test("observer UI is read-only and receives only its authorized table projection", async ({
  baseURL,
  browser,
  request
}) => {
  const suffix = Date.now().toString(36);
  const campaign = await postJson<{ id: string }>(request, "/campaigns", {
    title: `观战边界验收 ${suffix}`,
    system: "coc7"
  });
  const session = await postJson<{
    access_token: string;
    join_code: string;
    member: { id: string };
  }>(request, `/campaigns/${campaign.id}/sessions`, { kp_display_name: "观战测试 KP" });
  const kpHeaders = { Authorization: `Bearer ${session.access_token}` };
  const player = await postJson<{ access_token: string; member: { id: string } }>(
    request,
    "/sessions/join",
    { join_code: session.join_code, display_name: "桌内玩家", role: "player" }
  );
  const observer = await postJson<{ access_token: string; member: { id: string } }>(
    request,
    "/sessions/join",
    { join_code: session.join_code, display_name: "只读观战者", role: "observer" }
  );
  const playerHeaders = { Authorization: `Bearer ${player.access_token}` };

  await postJson(request, `/campaigns/${campaign.id}/messages`, {
    audience: "announcement",
    content: "这是公开公告。",
    recipient_member_id: null,
    client_message_id: `announcement-${suffix}`
  }, kpHeaders);
  await postJson(request, `/campaigns/${campaign.id}/messages`, {
    audience: "party",
    content: "这是观战者不可见的队伍秘密。",
    recipient_member_id: null,
    client_message_id: `party-${suffix}`
  }, playerHeaders);
  await postJson(request, `/campaigns/${campaign.id}/messages`, {
    audience: "direct",
    content: "这是玩家给 KP 的私信。",
    recipient_member_id: session.member.id,
    client_message_id: `direct-${suffix}`
  }, playerHeaders);

  const observerContext = await browser.newContext({ baseURL, locale: "zh-CN" });
  const kpContext = await browser.newContext({ baseURL, locale: "zh-CN" });
  await authenticate(observerContext, campaign.id, observer.access_token);
  await authenticate(kpContext, campaign.id, session.access_token);
  const observerPage = await observerContext.newPage();
  const kpPage = await kpContext.newPage();

  try {
    await Promise.all([observerPage.goto("/play"), kpPage.goto("/play")]);
    await expect(observerPage.getByRole("heading", { name: "消息与私密边界" })).toBeVisible();
    await expect(observerPage.getByText("这是公开公告。")).toBeVisible();
    await expect(observerPage.getByText("这是观战者不可见的队伍秘密。")).toHaveCount(0);
    await expect(observerPage.getByText("这是玩家给 KP 的私信。")).toHaveCount(0);
    await expect(observerPage.getByLabel("发送范围")).toBeDisabled();
    await expect(observerPage.getByLabel("玩家行动")).toHaveCount(0);
    await expect(observerPage.getByRole("link", { name: /模型设置/ })).toHaveCount(0);
    await expect(observerPage.getByRole("link", { name: /地图棋子/ })).toHaveCount(0);

    await observerPage.getByLabel("向 KP 私信").fill("观战者请求暂时离席。");
    await observerPage.getByRole("button", { name: "发送", exact: true }).click();
    await expect(observerPage.getByText("观战者请求暂时离席。")).toBeVisible();
    await expect(kpPage.getByText("观战者请求暂时离席。")).toBeVisible({ timeout: 8_000 });
  } finally {
    await Promise.all([observerContext.close(), kpContext.close()]);
  }
});
