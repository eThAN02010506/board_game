import { test } from "@playwright/test";
import {
  closeFullAiCampaign,
  createFullAiCampaign
} from "./support/fullAiCampaign";
import { playRulesetCombatFromUi } from "./support/longCampaignCombat";

test.setTimeout(300_000);

test("KP and player complete a ruleset-owned combat entirely through UI", async ({
  browser
}) => {
  const campaign = await createFullAiCampaign(browser, {
    campaignTitle: `长团战斗 ${Date.now().toString(36)}`,
    playerCount: 1
  });
  try {
    await playRulesetCombatFromUi(
      campaign,
      campaign.players[0],
      `仓库冲突-${Date.now().toString(36)}`
    );
  } finally {
    await closeFullAiCampaign(campaign);
  }
});
