import { expect, test, type APIRequestContext } from "@playwright/test";

async function postJson<T>(
  request: APIRequestContext,
  path: string,
  data: unknown,
  headers: Record<string, string> = {}
): Promise<T> {
  const response = await request.post(`/api${path}`, { data, headers });
  expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy();
  return response.json() as Promise<T>;
}

function investigatorSheet(name: string) {
  return {
    schema_version: "coc7-investigator-v1",
    ruleset_id: "coc7-keeper-cn-2002c",
    identity: { name, occupation: "记者", age: 30, era: "1920s" },
    characteristics: {
      str: 50, con: 50, siz: 50, dex: 50, app: 50,
      int: 50, pow: 50, edu: 50, luck: 50
    },
    skills: [{
      skill_key: "e2e.spot_hidden",
      display_name: "侦查",
      base_value: 60,
      occupation_points: 0,
      interest_points: 0,
      development_points: 0
    }],
    assets: { items: [] },
    background: {},
    provenance: { source_type: "e2e" }
  };
}

test("a seated player safely reviews Auto KP when the model is unavailable", async ({
  page,
  request
}) => {
  const suffix = Date.now().toString(36);
  const campaign = await postJson<{ id: string }>(request, "/campaigns", {
    title: `无 KP 浏览器验收 ${suffix}`,
    system: "coc7",
    current_time: "1928-10-03 19:30"
  });
  const sessionBundle = await postJson<{
    access_token: string;
    member: { id: string };
    session: { id: string };
  }>(request, `/campaigns/${campaign.id}/sessions`, {
    kp_display_name: "E2E KP"
  });
  const kpHeaders = { Authorization: `Bearer ${sessionBundle.access_token}` };
  const seatBundle = await postJson<{
    invitation_code: string;
  }>(request, `/sessions/${sessionBundle.session.id}/seats`, {
    label: "浏览器玩家席位"
  }, kpHeaders);
  const profileBundle = await postJson<{
    player_token: string;
  }>(request, "/player-profiles", { display_name: `玩家 ${suffix}` });
  const playerHeaders = { "X-AI-KP-Player-Token": profileBundle.player_token };
  const claimed = await postJson<{
    access_token: string;
    member: { id: string };
  }>(request, "/session-seats/claim", {
    invitation_code: seatBundle.invitation_code,
    display_name: `玩家 ${suffix}`
  }, playerHeaders);
  const authenticatedPlayerHeaders = {
    ...playerHeaders,
    Authorization: `Bearer ${claimed.access_token}`
  };
  const investigator = await postJson<{
    id: string;
    current_revision_id: string;
  }>(request, "/investigators", {
    canonical_sheet: investigatorSheet(`林若川 ${suffix}`),
    source_type: "manual"
  }, authenticatedPlayerHeaders);
  await postJson(request,
    `/campaigns/${campaign.id}/investigators/${investigator.id}/submit`,
    { revision_id: investigator.current_revision_id },
    authenticatedPlayerHeaders
  );
  const approval = await postJson<{ legacy_pc_id: string }>(request,
    `/campaigns/${campaign.id}/investigators/${investigator.id}/review`,
    { action: "approved", comment: "浏览器无 KP 验收" },
    kpHeaders
  );
  await postJson(request,
    `/sessions/${sessionBundle.session.id}/members/${claimed.member.id}/assign-investigator`,
    { investigator_id: investigator.id },
    kpHeaders
  );
  const module = await postJson<{ id: string }>(
    request,
    `/campaigns/${campaign.id}/modules`,
    {
      title: "钟楼入口",
      text: "@visibility=kp @spoiler=act-1\n钟楼门前有碎玻璃。",
      source_type: "plaintext"
    },
    kpHeaders
  );
  const run = await postJson<{ id: string; version: number }>(
    request,
    `/campaigns/${campaign.id}/module-runs`,
    { module_id: module.id, current_scene_key: "clocktower", active_spoiler_tags: ["act-1"] },
    kpHeaders
  );
  await postJson(request, `/module-runs/${run.id}/automation`, {
    expected_version: run.version,
    level: "ai_kp",
    reason: "浏览器无 KP 验收"
  }, kpHeaders);

  await page.addInitScript(({ campaignId, accessToken, playerToken }) => {
    sessionStorage.setItem(`ai-kp-session-token:${campaignId}`, accessToken);
    localStorage.setItem("ai-kp-player-profile-token", playerToken);
  }, {
    campaignId: campaign.id,
    accessToken: claimed.access_token,
    playerToken: profileBundle.player_token
  });
  await page.goto("/play");

  await expect(page.getByRole("heading", { name: "你的行动桌面" })).toBeVisible();
  await expect(page.getByText("实时同步", { exact: true })).toBeVisible();
  await page.getByLabel("玩家行动").fill("我检查钟楼入口是否有危险。 ");
  await page.getByRole("button", { name: "提交并后台推进" }).click();

  const jobs = page.getByLabel("自动 KP 任务");
  await expect(jobs).toBeVisible();
  await expect(jobs).toContainText("succeeded", { timeout: 20_000 });
  await expect(page.getByText("需要 RP / 补充说明")).toBeVisible();
  await expect(page.getByText("AI 初步裁定 · 尚未执行")).toBeVisible();
  await expect(page.getByRole("button", { name: "确认此裁定" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "修改行动并重新裁定" })).toBeVisible();
  await expect(page.getByText("你的行动桌面")).toBeVisible();

  const checkedAction = await postJson<{ id: string }>(
    request,
    `/campaigns/${campaign.id}/actions`,
    {
      action_text: "我仔细检查钟楼门锁。",
      client_action_id: `browser-check-${suffix}`
    },
    authenticatedPlayerHeaders
  );
  const checkProposal = await postJson<{ id: string }>(
    request,
    `/campaigns/${campaign.id}/proposals`,
    {
      player_action_id: checkedAction.id,
      player_action: "我仔细检查钟楼门锁。",
      public_narration: "请进行侦查检定。",
      pc_id: approval.legacy_pc_id,
      proposed_checks: [{
        skill: "侦查",
        difficulty: "regular",
        reason: "检查门锁",
        pc_id: approval.legacy_pc_id,
        hidden: false
      }]
    },
    kpHeaders
  );
  await postJson(
    request,
    `/kp/proposals/${checkProposal.id}/approve`,
    { note: "浏览器检定验收" },
    kpHeaders
  );

  await page.getByRole("tab", { name: "检定" }).click();
  const rollButton = page.getByRole("button", { name: "数字骰" });
  await expect(rollButton).toBeVisible();
  await rollButton.click();
  await expect(page.getByRole("button", { name: "重放校验" })).toBeVisible();
  await expect.poll(async () => {
    const response = await request.get(`/api/player-actions/${checkedAction.id}`, {
      headers: authenticatedPlayerHeaders
    });
    return (await response.json() as { status: string }).status;
  }, { timeout: 20_000 }).toBe("resolved");
});
