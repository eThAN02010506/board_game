import { expect } from "@playwright/test";
import type { FullAiCampaign, FullAiPlayer } from "./fullAiCampaign";

export async function registerAndPickUpLootFromUi(
  campaign: FullAiCampaign,
  player: FullAiPlayer,
  lootName: string
): Promise<void> {
  await campaign.kpPage.goto("/play");
  const kpInventory = campaign.kpPage.getByRole("region", { name: "物品与资产" });
  await kpInventory.getByText("登记公开战利品", { exact: true }).click();
  await kpInventory.getByLabel("战利品名称").fill(lootName);
  await kpInventory.getByLabel("战利品公开说明").fill(
    "冲突结束后留在现场、可由玩家公开拾取的一件普通物品。"
  );
  await kpInventory.getByLabel("战利品数量").fill("1");
  await kpInventory.getByRole("button", { name: "登记到权威账本" }).click();
  await expect(kpInventory.locator(".inventory-card").filter({ hasText: lootName }))
    .toBeVisible({ timeout: 15_000 });

  await player.page.goto("/play");
  const playerInventory = player.page.getByRole("region", { name: "物品与资产" });
  await playerInventory.getByRole("button", { name: "刷新物品账本" }).click();
  const loot = playerInventory.locator(".inventory-card").filter({ hasText: lootName });
  await expect(loot).toContainText("· loot", { timeout: 15_000 });
  await loot.getByRole("button", { name: "拾取" }).click();
  await expect(loot).toContainText("· investigator", { timeout: 15_000 });
}
