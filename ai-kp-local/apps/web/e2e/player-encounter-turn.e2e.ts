import { expect, test, type APIRequestContext } from "@playwright/test";

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

function investigatorSheet(name: string) {
  return {
    schema_version: "coc7-investigator-v1",
    ruleset_id: "coc7-keeper-cn-2002c",
    identity: { name, occupation: "记者", age: 30, era: "1920s" },
    characteristics: {
      str: 50, con: 50, siz: 50, dex: 70, app: 50,
      int: 60, pow: 55, edu: 60, luck: 50
    },
    skills: [{
      skill_key: "fighting_brawl",
      display_name: "格斗（斗殴）",
      base_value: 65,
      occupation_points: 0,
      interest_points: 0,
      development_points: 0
    }],
    assets: { items: [] },
    background: {},
    provenance: { source_type: "encounter-e2e" }
  };
}

test("player previews, edits, and confirms a ruleset-owned encounter turn", async ({
  context,
  page,
  request
}) => {
  const suffix = Date.now().toString(36);
  const campaign = await postJson<{ id: string }>(request, "/campaigns", {
    title: `遭遇回合 UI 验收 ${suffix}`,
    system: "coc7"
  });
  const session = await postJson<{
    access_token: string;
    join_code: string;
    session: { id: string };
  }>(request, `/campaigns/${campaign.id}/sessions`, { kp_display_name: "遭遇 KP" });
  const profile = await postJson<{ player_token: string }>(
    request,
    "/player-profiles",
    { display_name: `玩家 ${suffix}` }
  );
  const joined = await postJson<{ access_token: string; member: { id: string } }>(
    request,
    "/sessions/join",
    { join_code: session.join_code, display_name: `玩家 ${suffix}` }
  );
  const kpHeaders = { Authorization: `Bearer ${session.access_token}` };
  const playerHeaders = {
    Authorization: `Bearer ${joined.access_token}`,
    "X-AI-KP-Player-Token": profile.player_token
  };
  const investigator = await postJson<{ id: string; current_revision_id: string }>(
    request,
    "/investigators",
    { canonical_sheet: investigatorSheet(`林若川 ${suffix}`), source_type: "manual" },
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
    { action: "approved", comment: "遭遇 UI 验收" },
    kpHeaders
  );
  await postJson(
    request,
    `/sessions/${session.session.id}/members/${joined.member.id}/assign-investigator`,
    { investigator_id: investigator.id },
    kpHeaders
  );
  const sessionZero = await request.get(`/api/campaigns/${campaign.id}/session-zero`, {
    headers: kpHeaders
  });
  const revision = (await sessionZero.json() as {
    revision: { id: string; version: number };
  }).revision;
  for (const headers of [kpHeaders, playerHeaders]) {
    await postJson(
      request,
      `/campaigns/${campaign.id}/session-zero/confirm`,
      { revision_id: revision.id, expected_version: revision.version },
      headers
    );
  }
  const damagedStateResponse = await request.get(
    `/api/campaigns/${campaign.id}/investigators/${investigator.id}/coc7/state`,
    { headers: playerHeaders }
  );
  expect(damagedStateResponse.ok()).toBeTruthy();
  const damagedState = (await damagedStateResponse.json() as {
    state: { state_version: number };
  }).state;
  await postJson(
    request,
    `/campaigns/${campaign.id}/investigators/${investigator.id}/coc7/commands`,
    {
      command_id: `combat-ui-damage-${suffix}`,
      expected_version: damagedState.state_version,
      command_type: "damage",
      payload: { damage: 2 },
      visibility: "table"
    },
    kpHeaders
  );
  await postJson(request, `/campaigns/${campaign.id}/inventory/items`, {
    command_id: `combat-ui-bandage-${suffix}`,
    item_type: "consumable",
    public_name: "随身急救包",
    public_description: "可在冲突中使用的一次性急救用品。",
    quantity: 2,
    holder_kind: "investigator",
    holder_id: investigator.id,
    use_effect: {
      kind: "ruleset_character_command",
      command_type: "first_aid",
      payload: { passed: true }
    },
    reason: "通用战斗恢复 UI 验收"
  }, kpHeaders);
  const threatNpc = await postJson<{ id: string }>(
    request,
    `/campaigns/${campaign.id}/npcs`,
    {
      name: "闯入者",
      profession: "不明武装人员",
      public_notes: "携带一只可见的旧皮包。",
      secret_notes: "仅供 KP 的动机不会进入玩家战斗投影。"
    },
    kpHeaders
  );
  await postJson(request, `/campaigns/${campaign.id}/inventory/items`, {
    command_id: `combat-ui-loot-${suffix}`,
    item_type: "gear",
    public_name: "旧皮包",
    public_description: "闯入者失去行动能力后留下的普通皮包。",
    publicly_listed: true,
    quantity: 1,
    is_unique: true,
    holder_kind: "npc",
    holder_id: threatNpc.id,
    source_refs: [{ kind: "encounter", id: `combat-ui-${suffix}` }],
    reason: "通用遭遇战利品 UI 验收"
  }, kpHeaders);
  const encounter = await postJson<{ id: string }>(request, `/campaigns/${campaign.id}/coc7/encounters`, {
    kind: "combat",
    title: "车厢内的对峙",
    participants: [
      {
        participant_id: "player-actor",
        name: "林若川",
        investigator_id: investigator.id,
        side: "party",
        dex: 70,
        action_profiles: [{
          action_key: "close_shot",
          label: "近距离射击",
          kind: "firearm",
          skill_target: 99,
          damage_expression: "10"
        }]
      },
      {
        participant_id: "threat",
        name: "闯入者",
        npc_id: threatNpc.id,
        side: "opposition",
        dex: 30,
        max_hp: 1,
        current_hp: 1,
        dodge_target: 30,
        action_profiles: [{
          action_key: "knife",
          label: "短刀攻击",
          kind: "melee",
          skill_target: 45,
          damage_expression: "1d4"
        }]
      }
    ]
  }, kpHeaders);

  await context.addInitScript(({ campaignId, accessToken, playerToken }) => {
    sessionStorage.setItem(`ai-kp-session-token:${campaignId}`, accessToken);
    localStorage.setItem("ai-kp-player-profile-token", playerToken);
  }, {
    campaignId: campaign.id,
    accessToken: joined.access_token,
    playerToken: profile.player_token
  });
  await page.goto("/play");

  const aidCard = page.locator(".inventory-card").filter({ hasText: "随身急救包" });
  await expect(aidCard).toContainText("×2");
  await aidCard.getByRole("button", { name: "消耗 1" }).click();
  await expect(aidCard).toContainText("×1");

  let completed = false;
  for (let attempt = 0; attempt < 5 && !completed; attempt += 1) {
    await expect(page.getByRole("region", { name: "我的遭遇回合" })).toBeVisible({ timeout: 15_000 });
    await page.getByLabel("遭遇规则动作").selectOption("close_shot");
    await page.getByLabel("遭遇行动目标").selectOption("threat");
    await page.getByLabel("遭遇行动描述").fill(
      attempt === 0
        ? "我绕向左侧，确认后近距离开枪制止他。"
        : "上一枪未能制止他；我重新确认射线后再次射击。"
    );
    await page.getByRole("button", { name: "预览并手动确认" }).click();
    await expect(page.getByText("骰值尚未生成。")).toBeVisible();
    await expect(page.getByText("目标：闯入者")).toBeVisible();
    if (attempt === 0) {
      await page.getByRole("button", { name: "修改行动" }).click();
      await expect(page.getByLabel("遭遇行动描述")).toBeVisible();
      await page.getByLabel("遭遇行动描述").fill("我先示警，再瞄准他的持械手近距离开枪。");
      await page.getByRole("button", { name: "预览并手动确认" }).click();
    }
    await page.getByRole("button", { name: "确认并结算" }).click();
    await expect.poll(async () => {
      const response = await request.get(`/api/coc7/encounters/${encounter.id}`, {
        headers: playerHeaders
      });
      if (!response.ok()) return "waiting";
      const current = await response.json() as {
        status: string;
        state: { turn_order: string[] };
        turn_index: number;
      };
      if (current.status === "completed") return "completed";
      return current.state.turn_order[current.turn_index] === "player-actor"
        ? "player_turn"
        : "waiting";
    }, { timeout: 15_000 }).not.toBe("waiting");
    const currentResponse = await request.get(`/api/coc7/encounters/${encounter.id}`, {
      headers: playerHeaders
    });
    completed = (await currentResponse.json() as { status: string }).status === "completed";
  }
  expect(completed, "bounded retries should produce a non-fumble firearm result").toBeTruthy();
  await expect(page.getByText("遭遇已经由规则状态机结束。", { exact: false }))
    .toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/dodge_target|damage_expression|damage_rolls/)).toHaveCount(0);

  await page.getByRole("button", { name: "刷新物品账本" }).click();
  const lootCard = page.locator(".inventory-card").filter({ hasText: "旧皮包" });
  await expect(lootCard).toContainText("· loot");
  await lootCard.getByRole("button", { name: "拾取" }).click();
  await expect(lootCard).toContainText("· investigator");

  // A fresh page represents a disconnected/reconnected player client. The
  // encounter outcome, consumed quantity, and acquired loot must be durable.
  await page.close();
  const reconnected = await context.newPage();
  await reconnected.goto("/play");
  await expect(reconnected.getByText("遭遇已经由规则状态机结束。", { exact: false }))
    .toBeVisible({ timeout: 15_000 });
  await expect(reconnected.locator(".inventory-card").filter({ hasText: "随身急救包" }))
    .toContainText("×1");
  await expect(reconnected.locator(".inventory-card").filter({ hasText: "旧皮包" }))
    .toContainText("· investigator");
});
