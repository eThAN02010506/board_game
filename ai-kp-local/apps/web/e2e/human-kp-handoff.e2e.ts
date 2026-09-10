import { expect, test, type APIRequestContext } from "@playwright/test";

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

test("human KP takes control, records a fact, and hands AI back the confirmed state", async ({
  page,
  request
}) => {
  const suffix = Date.now().toString(36);
  const campaign = await postJson<{ id: string }>(request, "/campaigns", {
    title: `人工接管 UI 验收 ${suffix}`,
    system: "coc7",
    current_time: "1928-10-04 21:00"
  });
  const session = await postJson<{
    access_token: string;
    member: { id: string };
  }>(request, `/campaigns/${campaign.id}/sessions`, { kp_display_name: "接管 KP" });
  const kpHeaders = { Authorization: `Bearer ${session.access_token}` };
  const module = await postJson<{ id: string }>(
    request,
    `/campaigns/${campaign.id}/modules`,
    {
      title: "通用接管场景",
      text: "@visibility=kp\n调查地点处于夜间，入口仍然开放。",
      source_type: "plaintext"
    },
    kpHeaders
  );
  const run = await postJson<{ id: string }>(
    request,
    `/campaigns/${campaign.id}/module-runs`,
    { module_id: module.id, current_scene_key: "entry", active_spoiler_tags: [] },
    kpHeaders
  );

  await page.addInitScript(({ campaignId, accessToken }) => {
    sessionStorage.setItem(`ai-kp-session-token:${campaignId}`, accessToken);
  }, { campaignId: campaign.id, accessToken: session.access_token });
  await page.goto("/modules");

  const control = page.getByRole("region", { name: "导演控制权" });
  await expect(control).toBeVisible({ timeout: 15_000 });
  await control.getByLabel("切换理由").fill("真人 KP 接管并确认现场变化");
  await control.getByRole("button", { name: "人类 KP 接管" }).click();
  await expect(control).toContainText("人类 KP 完全接管");

  const blocked = await request.post(`/api/module-runs/${run.id}/director/analyze`, {
    data: { player_intent: "继续推进" },
    headers: kpHeaders
  });
  expect(blocked.status()).toBe(409);

  await page.getByRole("link", { name: "世界事实" }).click();
  await expect(page.getByRole("heading", { name: "追加事实" })).toBeVisible();
  await page.getByLabel("主体").fill("旧仓库侧门");
  await page.getByLabel("关系或属性").fill("当前状态");
  await page.getByLabel("内容").fill("已由人类 KP 确认上锁，锁孔留有新鲜划痕。");
  await page.getByLabel("世界时间（可选）").fill("1928-10-04 21:05");
  await page.getByRole("button", { name: "追加到事实账本" }).click();
  await expect(page.getByRole("button", { name: /公开事实 旧仓库侧门/ })).toBeVisible();
  await expect(page.getByText(/已由人类 KP 确认上锁/)).toBeVisible();

  await page.getByRole("link", { name: "KP 本" }).click();
  await expect(control).toBeVisible({ timeout: 15_000 });
  await control.getByLabel("切换理由").fill("权威事实已记录，从确认后的状态恢复");
  await control.getByRole("button", { name: "交还 AI 辅助" }).click();
  await expect(control).toContainText("AI 可辅助");

  const state = await request.get(`/api/module-runs/${run.id}/director-state`, {
    headers: kpHeaders
  });
  expect(state.ok(), await state.text()).toBeTruthy();
  const stateBody = await state.json() as {
    run: { director_control_mode: string };
    control_events: Array<{ from_mode: string; to_mode: string; reason: string }>;
  };
  expect(stateBody.run.director_control_mode).toBe("ai_assist");
  expect(stateBody.control_events.map((event) => [event.from_mode, event.to_mode])).toEqual([
    ["ai_assist", "human_kp"],
    ["human_kp", "ai_assist"]
  ]);

  const facts = await request.get(`/api/campaigns/${campaign.id}/facts`, {
    headers: kpHeaders
  });
  expect(facts.ok(), await facts.text()).toBeTruthy();
  expect(JSON.stringify(await facts.json())).toContain("已由人类 KP 确认上锁");
});
