import {
  expect,
  type Browser,
  type BrowserContext,
  type Locator,
  type Page
} from "@playwright/test";
import type { AuthorityRefCollector } from "./authorityEvidence";

const ATTRIBUTES = ["STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU", "LUCK"];

async function withOpenDetails(
  details: Locator,
  action: (details: Locator) => Promise<void>
): Promise<void> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (!await details.evaluate((element) => (element as HTMLDetailsElement).open)) {
      await details.locator("summary").click();
    }
    try {
      await action(details);
      return;
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

export type FullAiPlayer = {
  context: BrowserContext;
  page: Page;
  displayName: string;
  investigatorName: string;
};

export type FullAiCampaign = {
  campaignTitle: string;
  kpContext: BrowserContext;
  kpPage: Page;
  players: FullAiPlayer[];
};

async function claimSeat(
  context: BrowserContext,
  invitation: string,
  displayName: string,
  authorityRefs?: AuthorityRefCollector
): Promise<Page> {
  const page = await context.newPage();
  authorityRefs?.attach(page);
  await page.goto("/campaigns");
  // A fresh LAN player intentionally cannot enumerate campaigns. The
  // invitation is the capability that atomically reveals and selects exactly
  // one campaign, so claim it directly from the visitor form.
  await page.getByLabel("席位邀请码").fill(invitation);
  await page.getByLabel("玩家显示名").fill(displayName);
  await page.getByRole("button", { name: "认领并进入席位" }).click();
  await expect(page.getByText(displayName, { exact: true })).toBeVisible();
  return page;
}

export async function createAndSubmitInvestigator(
  page: Page,
  campaignTitle: string,
  playerName: string,
  investigatorName: string
) {
  await page.goto("/investigators");
  const createProfileButton = page.getByRole("button", { name: "建立档案" });
  if (await createProfileButton.isVisible()) {
    await page.getByLabel("玩家显示名").fill(playerName);
    await createProfileButton.click();
  }
  await expect(page.getByRole("heading", { name: "调查员编辑器" })).toBeVisible({
    timeout: 15_000
  });

  const editor = page.locator(".manual-character-card");
  await editor.getByLabel("姓名", { exact: true }).fill(investigatorName);
  await editor.getByLabel("玩家名", { exact: true }).fill(playerName);
  await editor.getByLabel("职业", { exact: true }).fill("记者");
  await editor.getByLabel("时代", { exact: true }).fill("1920s");
  await editor.getByLabel("年龄", { exact: true }).fill("28");
  for (const attribute of ATTRIBUTES) {
    await editor.getByLabel(attribute, { exact: true }).fill("60");
  }
  await editor.getByRole("button", { name: "生成并应用推荐加点" }).click();
  await expect(editor.getByRole("button", { name: "重新生成并应用" })).toBeVisible();
  await editor.getByRole("button", { name: "保存不可变草稿" }).click();
  const investigator = page.locator(".investigator-record").filter({ hasText: investigatorName });
  await expect(investigator).toBeVisible({ timeout: 15_000 });
  await investigator.getByRole("button", {
    name: `提交当前版本给 ${campaignTitle} KP`
  }).click();
  await expect(investigator.getByText("待 KP 审核", { exact: true })).toBeVisible({
    timeout: 15_000
  });
}

export async function approveInvestigator(
  kpPage: Page,
  investigatorName: string,
  playerDisplayName?: string
) {
  await kpPage.goto("/investigators");
  let record = kpPage.locator(".investigator-review-record").filter({
    hasText: investigatorName
  });
  await record.getByRole("button", { name: "批准当前版本" }).click();
  record = kpPage.locator(".investigator-review-record").filter({
    hasText: investigatorName
  });
  await expect(record.getByText("已批准", { exact: true }).first()).toBeVisible({
    timeout: 15_000
  });
  if (!playerDisplayName) return;
  await record.getByLabel("绑定到玩家席位").selectOption({ label: playerDisplayName });
  await record.getByRole("button", { name: "绑定已批准角色" }).click();
  await expect(record.getByLabel("绑定到玩家席位").locator("option:checked")).toHaveText(
    `${playerDisplayName}（已有角色）`,
    { timeout: 15_000 }
  );
}

export async function createFullAiCampaign(
  browser: Browser,
  options: {
    campaignTitle: string;
    playerCount?: number;
    worldview?: string;
    authorityRefs?: AuthorityRefCollector;
    authorizeKpPage?: (page: Page) => Promise<void>;
    reportStage?: (stage: string) => void;
  }
): Promise<FullAiCampaign> {
  const playerCount = options.playerCount ?? 4;
  const suffix = Date.now().toString(36);
  const kpContext = await browser.newContext({ locale: "zh-CN" });
  const playerContexts = await Promise.all(
    Array.from({ length: playerCount }, () => browser.newContext({ locale: "zh-CN" }))
  );
  const kpPage = await kpContext.newPage();
  options.authorityRefs?.attach(kpPage);
  const players: FullAiPlayer[] = [];
  const reportStage = options.reportStage ?? (() => undefined);

  try {
    reportStage("campaign setup: authorizing KP browser");
    await kpPage.goto("/campaigns");
    await options.authorizeKpPage?.(kpPage);
    await kpPage.getByLabel("团名").fill(options.campaignTitle);
    await kpPage.getByLabel("当前时间").fill("1925-10-31 18:00");
    await kpPage.getByRole("button", { name: "创建 Campaign" }).click();
    const campaignRecord = kpPage.getByRole("button", {
      name: new RegExp(options.campaignTitle)
    });
    await expect(campaignRecord).toBeVisible({ timeout: 30_000 });
    // Creating a campaign normally starts its KP session.  If the initial
    // campaign-list refresh wins that client-side race, recover through the
    // same explicit control a real KP sees instead of using an API shortcut.
    const kpRoleBadge = kpPage.locator(".role-badge.kp");
    const automaticSessionReady = await kpRoleBadge
      .waitFor({ state: "visible", timeout: 10_000 })
      .then(() => true)
      .catch(() => false);
    if (!automaticSessionReady) {
      await campaignRecord.click();
      const legacyTools = kpPage.getByText("创建 KP 会话或使用旧版共享码", {
        exact: true
      });
      await legacyTools.click();
      await kpPage.getByRole("button", { name: "为当前团开启 KP 会话" }).click();
    }
    await expect(kpRoleBadge).toHaveText("KP", { timeout: 30_000 });

    reportStage("campaign setup: creating player invitations");
    const invitations: string[] = [];
    for (let index = 0; index < playerCount; index += 1) {
      const seatLabel = `调查员席位 ${index + 1}`;
      await kpPage.getByLabel("席位名称").fill(seatLabel);
      await kpPage.getByRole("button", { name: "创建单席邀请" }).click();
      const seat = kpPage.locator(".seat-card").filter({ hasText: seatLabel });
      invitations.push((await seat.locator("code").innerText()).trim());
    }

    for (let index = 0; index < playerCount; index += 1) {
      reportStage(`campaign setup: player ${index + 1}/${playerCount} claiming seat and submitting investigator`);
      const displayName = `玩家${index + 1}-${suffix}`;
      const investigatorName = `调查员${index + 1}-${suffix}`;
      const page = await claimSeat(
        playerContexts[index],
        invitations[index],
        displayName,
        options.authorityRefs
      );
      await createAndSubmitInvestigator(
        page,
        options.campaignTitle,
        displayName,
        investigatorName
      );
      players.push({
        context: playerContexts[index],
        page,
        displayName,
        investigatorName
      });
    }

    reportStage("campaign setup: approving and binding investigators");
    for (const player of players) {
      await approveInvestigator(
        kpPage,
        player.investigatorName,
        player.displayName
      );
    }

    reportStage("campaign setup: publishing Session 0");
    await kpPage.goto("/play");
    const sessionZeroEditor = kpPage.locator("details").filter({
      has: kpPage.locator("summary").filter({ hasText: "Campaign 设置与公共边界" })
    });
    await withOpenDetails(sessionZeroEditor, async (editor) => {
      await editor.getByLabel("世界观").fill(
        options.worldview ?? "长期调查、克制恐怖、允许玩家自由发散与适度幽默",
        { timeout: 5_000 }
      );
    });
    await withOpenDetails(sessionZeroEditor, async (editor) => {
      await editor.getByLabel("主持模式").selectOption("ai_kp", { timeout: 5_000 });
    });
    // Changing the host mode refreshes the Session 0 projection and remounts
    // this uncontrolled <details>, which can legitimately collapse it again.
    // Re-open the newly rendered panel just as a real KP would before editing
    // the remaining field.
    await withOpenDetails(sessionZeroEditor, async (editor) => {
      await editor.getByLabel("预计玩家数").fill(String(playerCount), { timeout: 5_000 });
      await editor.getByRole("button", { name: "保存新版本并确认" }).click({ timeout: 5_000 });
    });
    await expect(kpPage.getByText(`1/${playerCount + 1}`, { exact: true })).toBeVisible();

    for (const [index, player] of players.entries()) {
      reportStage(`campaign setup: player ${index + 1}/${playerCount} confirming Session 0`);
      await player.page.goto("/campaigns");
      await player.page.getByRole("button", { name: "刷新身份与角色绑定" }).click();
      await player.page.goto("/play");
      await expect(player.page.getByRole("button", { name: "确认当前 Session 0" })).toBeVisible({
        timeout: 15_000
      });
      await player.page.getByRole("button", { name: "确认当前 Session 0" }).click();
    }

    await expect(kpPage.getByText("全桌已确认")).toBeVisible({ timeout: 10_000 });
    reportStage("campaign setup: complete");
    // Action readiness is asserted by submitPlayerAction after the module run is
    // published and bound. At this setup boundary a final player projection
    // refresh may legitimately precede or follow the KP's confirmation refresh.
    return { campaignTitle: options.campaignTitle, kpContext, kpPage, players };
  } catch (error) {
    await Promise.all(playerContexts.map((context) => context.close()));
    await kpContext.close();
    throw error;
  }
}

export async function addPlayerToFullAiCampaign(
  browser: Browser,
  campaign: FullAiCampaign,
  options: {
    authorityRefs?: AuthorityRefCollector;
    reportStage?: (stage: string) => void;
  } = {}
): Promise<FullAiPlayer> {
  const suffix = Date.now().toString(36);
  const displayName = `中途加入玩家-${suffix}`;
  const investigatorName = `中途加入调查员-${suffix}`;
  const seatLabel = `中途加入席位-${suffix}`;
  const context = await browser.newContext({ locale: "zh-CN" });
  options.reportStage?.("late join: creating one-seat invitation");
  try {
    await campaign.kpPage.goto("/campaigns");
    await campaign.kpPage.getByLabel("席位名称").fill(seatLabel);
    await campaign.kpPage.getByRole("button", { name: "创建单席邀请" }).click();
    const seat = campaign.kpPage.locator(".seat-card").filter({ hasText: seatLabel });
    const invitation = (await seat.locator("code").innerText()).trim();
    const page = await claimSeat(
      context,
      invitation,
      displayName,
      options.authorityRefs
    );
    await createAndSubmitInvestigator(
      page,
      campaign.campaignTitle,
      displayName,
      investigatorName
    );
    await approveInvestigator(campaign.kpPage, investigatorName, displayName);
    await page.goto("/play");
    const confirm = page.getByRole("button", { name: "确认当前 Session 0" });
    await expect(confirm).toBeVisible({ timeout: 15_000 });
    await confirm.click();
    await expect(page.getByText("全桌已确认")).toBeVisible({ timeout: 15_000 });
    const player = { context, page, displayName, investigatorName };
    campaign.players.push(player);
    options.reportStage?.("late join: player approved, bound, and Session 0 confirmed");
    return player;
  } catch (error) {
    await context.close();
    throw error;
  }
}

export async function closeFullAiCampaign(campaign: FullAiCampaign) {
  await Promise.all(campaign.players.map((player) => player.context.close()));
  await campaign.kpContext.close();
}
