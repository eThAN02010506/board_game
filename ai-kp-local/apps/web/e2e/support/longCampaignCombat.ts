import { expect } from "@playwright/test";
import type { FullAiCampaign, FullAiPlayer } from "./fullAiCampaign";

export async function playRulesetCombatFromUi(
  campaign: FullAiCampaign,
  player: FullAiPlayer,
  title: string,
  options: { improvisedAgent?: boolean } = {}
): Promise<void> {
  const threatName = `规则威胁-${Date.now().toString(36)}`;
  await campaign.kpPage.goto("/play");
  const creator = campaign.kpPage.locator("details.gameplay-create");
  await creator.locator("summary").click();
  await creator.getByLabel("标题").fill(title);
  await creator.getByLabel("NPC / 威胁").fill(threatName);
  await creator.getByLabel("NPC DEX").fill("1");
  await creator.getByLabel("NPC HP").fill("1");
  await creator.getByLabel("NPC 攻击技能值").fill("25");
  await creator.getByLabel("NPC 伤害骰").fill("1");
  await creator.getByLabel(player.investigatorName, { exact: true }).check();
  await creator.getByRole("button", { name: "创建战斗" }).click();
  await expect(campaign.kpPage.getByText(title, { exact: true }).first()).toBeVisible({
    timeout: 15_000
  });

  await player.page.goto("/play");
  const turn = player.page.getByRole("region", { name: "我的遭遇回合" });
  await expect(turn).toBeVisible({ timeout: 15_000 });
  const action = turn.getByLabel("遭遇规则动作");
  const optionValues = await action.locator("option").evaluateAll((options) =>
    options.map((option) => (option as HTMLOptionElement).value)
  );
  const actionKey = options.improvisedAgent
    ? optionValues.find((value) => value === "improvised")
    : optionValues.find((value) => value && value !== "improvised");
  if (!actionKey) {
    throw new Error(
      options.improvisedAgent
        ? "ruleset exposed no improvised combat action"
        : "ruleset exposed no deterministic player combat action"
    );
  }
  await action.selectOption(actionKey);
  const target = turn.getByLabel("遭遇行动目标");
  if (await target.isVisible().catch(() => false)) {
    await target.selectOption({ label: threatName });
  }
  await turn.getByLabel("遭遇行动描述").fill(
    "我先确认同伴不在攻击路径上，再用角色规则动作制止眼前威胁。"
  );
  await turn.getByRole("button", { name: "预览并手动确认" }).click();
  if (options.improvisedAgent) {
    const propose = turn.getByRole("button", { name: "让遭遇 Agent 提案" });
    await expect(propose).toBeVisible({ timeout: 15_000 });
    await propose.click();
  }
  await expect(turn.getByRole("button", { name: "确认并结算" })).toBeVisible({
    timeout: options.improvisedAgent ? 360_000 : 15_000
  });
  await turn.getByRole("button", { name: "确认并结算" }).click();

  await campaign.kpPage.goto("/play");
  await campaign.kpPage.getByRole("button", { name: new RegExp(title) }).first().click();
  const end = campaign.kpPage.locator(".encounter-console").getByRole("button", {
    name: "结束",
    exact: true
  });
  if (await end.isVisible().catch(() => false)) await end.click();
  await player.page.goto("/play");
  await expect(player.page.getByText("遭遇已经由规则状态机结束。", { exact: false }))
    .toBeVisible({ timeout: 15_000 });
}
