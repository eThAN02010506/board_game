import { expect } from "@playwright/test";
import type { FullAiCampaign } from "./fullAiCampaign";

export async function exerciseAiAssistedGmHandoff(
  campaign: FullAiCampaign,
  factSuffix: string
): Promise<void> {
  await campaign.kpPage.goto("/modules");
  const control = campaign.kpPage.getByRole("region", { name: "导演控制权" });
  await expect(control).toBeVisible({ timeout: 15_000 });
  await control.getByLabel("切换理由").fill("AI-assisted GM 检查并纠正一项公开现场事实");
  await control.getByRole("button", { name: "人类 KP 接管" }).click();
  await expect(control).toContainText("人类 KP 完全接管");

  await campaign.kpPage.getByRole("link", { name: "世界事实" }).click();
  await expect(campaign.kpPage.getByRole("heading", { name: "追加事实" })).toBeVisible();
  await campaign.kpPage.getByLabel("主体").fill(`长团交接点-${factSuffix}`);
  await campaign.kpPage.getByLabel("关系或属性").fill("当前状态");
  await campaign.kpPage.getByLabel("内容").fill(
    "已由 AI-assisted GM 从玩家可见进展核对，并作为公开权威事实提交。"
  );
  await campaign.kpPage.getByRole("button", { name: "追加到事实账本" }).click();
  await expect(campaign.kpPage.getByText(/已由 AI-assisted GM/)).toBeVisible();

  await campaign.kpPage.getByRole("link", { name: "KP 本" }).click();
  await expect(control).toBeVisible({ timeout: 15_000 });
  await control.getByLabel("切换理由").fill("纠正已进入权威账本，从确认后的状态交还 AI");
  await control.getByRole("button", { name: "交还 AI 辅助" }).click();
  await expect(control).toContainText("AI 可辅助");
}
