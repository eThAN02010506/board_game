import { expect, type Page } from "@playwright/test";

export type PlayerStandardUiEvent = {
  kind:
    | "current_situation_visible"
    | "action_space_visible"
    | "next_step_visible"
    | "character_state_visible";
  session_index: number;
};

export async function observePlayerStandardBaseline(
  page: Page,
  sessionIndex: number
): Promise<PlayerStandardUiEvent[]> {
  await page.goto("/play");
  await expect(page.getByRole("heading", { name: "Session 与继续游戏" })).toBeVisible({
    timeout: 15_000
  });
  await expect(page.locator(".public-turn-card").first()).toBeVisible({ timeout: 15_000 });
  const action = page.getByLabel("玩家行动");
  await expect(action).toBeVisible();
  await expect(action).toBeEnabled();
  await expect(page.getByText(/AI KP 自动推进|提交给 KP/).first()).toBeVisible();
  await expect(page.locator(".gameplay-character-state")).toContainText(/HP \d+ · SAN \d+/);
  return [
    { kind: "current_situation_visible", session_index: sessionIndex },
    { kind: "action_space_visible", session_index: sessionIndex },
    { kind: "next_step_visible", session_index: sessionIndex },
    { kind: "character_state_visible", session_index: sessionIndex }
  ];
}
