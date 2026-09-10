import { expect, test } from "@playwright/test";
import { closeFullAiCampaign, createFullAiCampaign } from "./support/fullAiCampaign";

test.setTimeout(300_000);

const PLAYER_COUNT = Number(process.env.AI_KP_ONBOARDING_PLAYERS ?? "4");

test("four players complete setup and Session 0 entirely through the UI", async ({ browser }) => {
  const suffix = Date.now().toString(36);
  const campaignTitle = `Full AI 长团 ${suffix}`;
  let campaign: Awaited<ReturnType<typeof createFullAiCampaign>> | undefined;

  try {
    campaign = await createFullAiCampaign(browser, {
      campaignTitle,
      playerCount: PLAYER_COUNT
    });
    await expect(campaign.kpPage.getByText("全桌已确认")).toBeVisible();
    for (const player of campaign.players) {
      await expect(player.page.getByLabel("玩家行动")).toBeEnabled();
    }
  } finally {
    if (campaign) await closeFullAiCampaign(campaign);
  }
});
