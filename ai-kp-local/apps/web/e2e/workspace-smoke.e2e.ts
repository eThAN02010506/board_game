import { expect, test } from "@playwright/test";

test("backend proxy and guest onboarding remain navigable without privileged links", async ({ page, request }) => {
  const health = await request.get("/api/health");
  expect(health.ok()).toBeTruthy();
  await expect(health.json()).resolves.toEqual({ ok: true });

  await page.goto("/play");
  await expect(page.locator(".brand")).toContainText("AI KP Local");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  await expect(page.getByRole("navigation").getByRole("link")).toHaveCount(1);
  await page.getByRole("navigation").getByRole("link", { name: "团与权限" }).click();
  await expect(page).toHaveURL(/\/campaigns$/);
  await expect(page.getByText("团与权限", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "模型设置" })).toHaveCount(0);
});

test("unknown guest paths fall back to onboarding without a blank screen", async ({ page }) => {
  await page.goto("/does-not-exist");
  await expect(page.locator(".brand")).toContainText("AI KP Local");
  await expect(
    page.getByRole("navigation").getByRole("link", { name: "团与权限" })
  ).toHaveAttribute(
    "aria-current",
    "page"
  );
});

test("campaign workspace reflows into one column on a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/campaigns");

  await expect(page.locator(".sidebar")).toBeVisible();
  await expect(page.getByRole("navigation")).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.locator(".app-shell")).toHaveCSS("grid-template-columns", "390px");
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
  ).toBeTruthy();
});
