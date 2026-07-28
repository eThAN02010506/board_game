import { expect, test } from "@playwright/test";

test("backend proxy and primary workspaces remain navigable", async ({ page, request }) => {
  const health = await request.get("/api/health");
  expect(health.ok()).toBeTruthy();
  await expect(health.json()).resolves.toEqual({ ok: true });

  await page.goto("/play");
  await expect(page.locator(".brand")).toContainText("AI KP Local");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  const workspaces = [
    ["团与权限", "/campaigns"],
    ["调查员", "/investigators"],
    ["地图棋子", "/maps"],
    ["规则知识", "/rules"],
    ["模型设置", "/models"],
    ["功能规划", "/planning"]
  ] as const;
  for (const [label, pathname] of workspaces) {
    await page.getByRole("button", { name: label }).click();
    await expect(page).toHaveURL(new RegExp(`${pathname}$`));
    await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  }
});

test("unknown paths fall back to the play workspace without a blank screen", async ({ page }) => {
  await page.goto("/does-not-exist");
  await expect(page.locator(".brand")).toContainText("AI KP Local");
  await expect(page.getByRole("button", { name: "游玩桌面" })).toHaveAttribute(
    "aria-pressed",
    "true"
  );
});
