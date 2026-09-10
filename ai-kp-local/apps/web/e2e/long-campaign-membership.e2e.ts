import { expect, test } from "@playwright/test";
import {
  addPlayerToFullAiCampaign,
  closeFullAiCampaign,
  createFullAiCampaign
} from "./support/fullAiCampaign";
import {
  movePlayerToObserver,
  temporarilyLeaveAndReturn
} from "./support/longCampaignLifecycle";

test.setTimeout(420_000);

test("a long campaign accepts a late player while an original player can leave", async ({
  browser
}) => {
  const campaign = await createFullAiCampaign(browser, {
    campaignTitle: `长期成员变更 ${Date.now().toString(36)}`,
    playerCount: 4
  });

  try {
    await temporarilyLeaveAndReturn(campaign, campaign.players[1]);
    const latePlayer = await addPlayerToFullAiCampaign(browser, campaign);
    await expect(latePlayer.page.getByLabel("玩家行动")).toBeEnabled({ timeout: 15_000 });

    await movePlayerToObserver(campaign, campaign.players[3]);
    await expect(campaign.players[3].page.getByRole("region", {
      name: "角色生命周期"
    })).toContainText("观战", { timeout: 15_000 });
    await expect(latePlayer.page.getByLabel("玩家行动")).toBeEnabled();
  } finally {
    await closeFullAiCampaign(campaign);
  }
});
