import { expect, test } from "@playwright/test";
import {
  closeFullAiCampaign,
  createFullAiCampaign
} from "./support/fullAiCampaign";
import {
  killAndReplaceInvestigator,
  prepareReplacementInvestigator
} from "./support/longCampaignLifecycle";

test.setTimeout(300_000);

test("a dead investigator can be replaced and the player continues from the UI", async ({
  browser
}) => {
  const suffix = Date.now().toString(36);
  const campaign = await createFullAiCampaign(browser, {
    campaignTitle: `角色生命周期 ${suffix}`,
    playerCount: 1
  });
  const player = campaign.players[0];
  const replacementName = `后备调查员-${suffix}`;

  try {
    await prepareReplacementInvestigator(campaign, player, replacementName);
    await killAndReplaceInvestigator(campaign, player, replacementName);
    await player.page.getByLabel("玩家行动").fill("后备调查员接手并继续调查。");
    await player.page.getByLabel("AI KP 自动推进").uncheck();
    await player.page.getByRole("button", { name: "提交给 KP" }).click();
    await expect(player.page.getByText("后备调查员接手并继续调查。", {
      exact: true
    })).toBeVisible();
  } finally {
    await closeFullAiCampaign(campaign);
  }
});
