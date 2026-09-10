// @vitest-environment node

import { afterEach, describe, expect, test, vi } from "vitest";

const originalManagedBackend = process.env.AI_KP_PLAYWRIGHT_MANAGED_BACKEND;
const originalReuseServers = process.env.AI_KP_PLAYWRIGHT_REUSE_SERVERS;

afterEach(() => {
  restoreEnvironment("AI_KP_PLAYWRIGHT_MANAGED_BACKEND", originalManagedBackend);
  restoreEnvironment("AI_KP_PLAYWRIGHT_REUSE_SERVERS", originalReuseServers);
  vi.resetModules();
});

describe("Playwright managed backend configuration", () => {
  test("keeps the ordinary backend and frontend web servers", async () => {
    delete process.env.AI_KP_PLAYWRIGHT_MANAGED_BACKEND;
    delete process.env.AI_KP_PLAYWRIGHT_REUSE_SERVERS;
    vi.resetModules();

    const { default: config } = await import("./playwright.config");
    expect(config.webServer).toEqual([
      expect.objectContaining({ name: "backend" }),
      expect.objectContaining({ name: "frontend" })
    ]);
  });

  test("removes only the backend webServer in managed mode", async () => {
    process.env.AI_KP_PLAYWRIGHT_MANAGED_BACKEND = "1";
    delete process.env.AI_KP_PLAYWRIGHT_REUSE_SERVERS;
    vi.resetModules();

    const { default: config } = await import("./playwright.config");
    expect(config.webServer).toEqual([expect.objectContaining({ name: "frontend" })]);
  });

  test("rejects managed backend and server reuse together at configuration load", async () => {
    process.env.AI_KP_PLAYWRIGHT_MANAGED_BACKEND = "1";
    process.env.AI_KP_PLAYWRIGHT_REUSE_SERVERS = "1";
    vi.resetModules();

    await expect(import("./playwright.config")).rejects.toThrow(
      /MANAGED_BACKEND=1 conflicts with AI_KP_PLAYWRIGHT_REUSE_SERVERS=1/
    );
  });
});

function restoreEnvironment(name: string, value: string | undefined): void {
  if (value === undefined) delete process.env[name];
  else process.env[name] = value;
}
