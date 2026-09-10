import { expect } from "@playwright/test";
import type { FullAiCampaign, FullAiPlayer } from "./fullAiCampaign";

export async function applyFirstAvailableGrowthFromUi(
  campaign: FullAiCampaign,
  players: readonly FullAiPlayer[]
): Promise<string> {
  for (const player of players) {
    await campaign.kpPage.goto("/play");
    const investigator = campaign.kpPage.getByRole("button", {
      name: new RegExp(player.investigatorName)
    }).first();
    if (!await investigator.isVisible().catch(() => false)) continue;
    await investigator.click();
    const characterState = campaign.kpPage.locator(".gameplay-character-state");
    await characterState.getByText("伤害、治疗、理智与成长", { exact: true }).click();
    await characterState.getByLabel("操作").selectOption("development");
    const skill = characterState.getByLabel("已标记技能");
    const values = await skill.locator("option").evaluateAll((options) =>
      options.map((option) => (option as HTMLOptionElement).value).filter(Boolean)
    );
    if (values.length === 0) continue;
    await skill.selectOption(values[0]);
    await characterState.getByLabel("成长 D100").fill("100");
    await characterState.getByLabel("增长 D10").fill("5");
    await characterState.getByRole("button", { name: "执行并记录" }).click();
    await expect(characterState).toContainText("成长待确认", { timeout: 15_000 });

    await player.page.goto("/investigators");
    const record = player.page.locator(".investigator-record").filter({
      hasText: player.investigatorName
    });
    await record.getByRole("button", { name: "跨团时间线" }).click();
    const proposal = record.locator(".permanent-change-decision").first();
    await expect(proposal).toBeVisible({ timeout: 15_000 });
    await proposal.getByLabel("玩家决定理由").fill(
      "接受本 Campaign 中由成功检定产生、并经规则插件结算的成长。"
    );
    await proposal.getByRole("button", { name: "接受变化" }).click();
    await expect(proposal).toHaveCount(0, { timeout: 15_000 });
    return player.investigatorName;
  }
  throw new Error(
    "no active investigator had a growth mark from a persisted successful check"
  );
}
