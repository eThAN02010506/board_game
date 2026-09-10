import { defineConfig, devices } from "@playwright/test";

const backendPort = process.env.AI_KP_PLAYWRIGHT_BACKEND_PORT ?? "8012";
const frontendPort = process.env.AI_KP_PLAYWRIGHT_FRONTEND_PORT ?? "5174";
const backendUrl = `http://127.0.0.1:${backendPort}`;
const frontendUrl = `http://127.0.0.1:${frontendPort}`;
const reuseExistingServer = process.env.AI_KP_PLAYWRIGHT_REUSE_SERVERS === "1";
const managedBackend = process.env.AI_KP_PLAYWRIGHT_MANAGED_BACKEND === "1";
const databasePath = process.env.AI_KP_PLAYWRIGHT_DB_PATH ?? ".playwright/ai-kp.sqlite3";

if (managedBackend && reuseExistingServer) {
  throw new Error(
    "AI_KP_PLAYWRIGHT_MANAGED_BACKEND=1 conflicts with AI_KP_PLAYWRIGHT_REUSE_SERVERS=1"
  );
}

const backendWebServer = {
  name: "backend",
  command:
    `.venv/bin/python -m uvicorn ai_kp.api.main:app --host 127.0.0.1 --port ${backendPort}`,
  cwd: "../..",
  env: {
    AI_KP_BACKUP_ROOT: ".playwright/backups",
    AI_KP_CORS_ORIGINS: frontendUrl,
    AI_KP_DB_PATH: databasePath,
    AI_KP_LLM_BASE_URL: "http://127.0.0.1:9/v1",
    AI_KP_LLM_MODEL: "e2e-unavailable-model",
    AI_KP_MAP_ASSET_ROOT: ".playwright/map-assets",
    AI_KP_MODULE_ASSET_ROOT: ".playwright/module-assets",
    AI_KP_RULEBOOK_INDEX_ROOT: ".playwright/rag",
    PYTHONPATH: "src"
  },
  url: `${backendUrl}/health`,
  reuseExistingServer,
  timeout: 120_000,
  stdout: "ignore" as const,
  stderr: "pipe" as const
};

const frontendWebServer = {
  name: "frontend",
  command: `pnpm dev --host 127.0.0.1 --port ${frontendPort}`,
  env: {
    VITE_BACKEND_TARGET: backendUrl
  },
  url: `${frontendUrl}/play`,
  reuseExistingServer,
  timeout: 120_000,
  stdout: "ignore" as const,
  stderr: "pipe" as const
};

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts",
  // E2E cases share one backend process and exercise its current-campaign state.
  // Serial workers keep browser contexts isolated without racing that shared state.
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
  use: {
    actionTimeout: 30_000,
    baseURL: frontendUrl,
    locale: "zh-CN",
    screenshot: "only-on-failure",
    trace: "on-first-retry",
    video: "retain-on-failure"
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] }
    }
  ],
  webServer: managedBackend
    ? [frontendWebServer]
    : [backendWebServer, frontendWebServer]
});
