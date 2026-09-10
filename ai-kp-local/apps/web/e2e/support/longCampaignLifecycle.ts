import { expect } from "@playwright/test";
import {
  approveInvestigator,
  createAndSubmitInvestigator,
  type FullAiCampaign,
  type FullAiPlayer
} from "./fullAiCampaign";

export async function prepareReplacementInvestigator(
  campaign: FullAiCampaign,
  player: FullAiPlayer,
  replacementName: string
): Promise<void> {
  await createAndSubmitInvestigator(
    player.page,
    campaign.campaignTitle,
    player.displayName,
    replacementName
  );
  await approveInvestigator(campaign.kpPage, replacementName);
}

export async function killAndReplaceInvestigator(
  campaign: FullAiCampaign,
  player: FullAiPlayer,
  replacementName: string
): Promise<void> {
  await campaign.kpPage.goto("/play");
  await campaign.kpPage.getByRole("button", {
    name: new RegExp(player.investigatorName)
  }).first().click();
  const characterState = campaign.kpPage.locator(".gameplay-character-state");
  await characterState.getByText("伤害、治疗、理智与成长", { exact: true }).click();
  await characterState.getByLabel("操作").selectOption("damage");
  await characterState.getByLabel("伤害", { exact: true }).fill("100");
  await characterState.getByRole("button", { name: "执行并记录" }).click();
  await expect(characterState).toContainText("死亡", { timeout: 15_000 });
  await proposeAndAcceptLifecycle(campaign, player, {
    action: "replace",
    reason: "规则状态已确认原调查员死亡，玩家换入已审核后备调查员继续 Campaign。",
    replacementName
  });
  player.investigatorName = replacementName;
  await expect(player.page.getByLabel("玩家行动")).toBeEnabled({ timeout: 15_000 });
}

export async function temporarilyLeaveAndReturn(
  campaign: FullAiCampaign,
  player: FullAiPlayer
): Promise<void> {
  await proposeAndAcceptLifecycle(campaign, player, {
    action: "temporary_leave",
    reason: "玩家按长期团计划暂时离席，并保留可审计回归路径。"
  });
  await expectObserverWorkspace(player);
  await proposeAndAcceptLifecycle(campaign, player, {
    action: "return",
    reason: "暂离玩家返回并继续使用原有已审核调查员。",
    replacementName: player.investigatorName
  });
  await expect(player.page.getByLabel("玩家行动")).toBeEnabled({ timeout: 15_000 });
}

export async function movePlayerToObserver(
  campaign: FullAiCampaign,
  player: FullAiPlayer
): Promise<void> {
  await proposeAndAcceptLifecycle(campaign, player, {
    action: "observe",
    reason: "玩家离开当前冒险并转为 Observer，Campaign 由其余玩家继续。"
  });
  await expectObserverWorkspace(player);
}

async function expectObserverWorkspace(player: FullAiPlayer): Promise<void> {
  await expect(player.page.getByText("观战工作台", { exact: true }).first()).toBeVisible({
    timeout: 15_000
  });
  await expect(player.page.getByLabel("玩家行动")).toHaveCount(0);
}

async function proposeAndAcceptLifecycle(
  campaign: FullAiCampaign,
  player: FullAiPlayer,
  options: {
    action: "replace" | "temporary_leave" | "return" | "observe";
    reason: string;
    replacementName?: string;
  }
): Promise<void> {
  await campaign.kpPage.goto("/play");
  const kpLifecycle = campaign.kpPage.getByRole("region", { name: "角色生命周期" });
  await kpLifecycle.getByRole("button", { name: "刷新角色生命周期" }).click();
  const memberOption = kpLifecycle.getByLabel("选择桌员").locator("option").filter({
    hasText: player.displayName
  });
  await kpLifecycle.getByLabel("选择桌员").selectOption(
    await memberOption.getAttribute("value") ?? ""
  );
  await kpLifecycle.getByLabel("选择生命周期动作").selectOption(options.action);
  if (options.replacementName) {
    await kpLifecycle.getByLabel("选择进入游戏的角色").selectOption({
      label: options.replacementName
    });
  }
  await kpLifecycle.getByLabel("生命周期变更原因").fill(options.reason);
  await kpLifecycle.getByRole("button", { name: "提交给玩家确认" }).click();

  await player.page.goto("/play");
  const playerLifecycle = player.page.getByRole("region", { name: "角色生命周期" });
  await playerLifecycle.getByRole("button", { name: "刷新角色生命周期" }).click();
  const request = playerLifecycle.locator(".lifecycle-requests article").filter({
    hasText: options.reason
  });
  await expect(request).toBeVisible({ timeout: 15_000 });
  await request.getByRole("button", { name: "确认" }).click();
}
