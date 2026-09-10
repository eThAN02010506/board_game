import { test } from "@playwright/test";
import {
  closeFullAiCampaign,
  createFullAiCampaign
} from "./support/fullAiCampaign";
import { registerAndPickUpLootFromUi } from "./support/longCampaignInventory";

test.setTimeout(300_000);

test("KP registers public loot and a player picks it up entirely through UI", async ({
  browser
}) => {
  const campaign = await createFullAiCampaign(browser, {
    campaignTitle: `长团物品 ${Date.now().toString(36)}`,
    playerCount: 1
  });
  try {
    await registerAndPickUpLootFromUi(
      campaign,
      campaign.players[0],
      `旧皮包-${Date.now().toString(36)}`
    );
  } finally {
    await closeFullAiCampaign(campaign);
  }
});
