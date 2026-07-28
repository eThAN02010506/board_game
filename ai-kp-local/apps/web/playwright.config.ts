import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
  use: {
    baseURL: "http://127.0.0.1:5174",
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
  webServer: [
    {
      name: "backend",
      command:
        ".venv/bin/python -m uvicorn ai_kp.api.main:app --host 127.0.0.1 --port 8012",
      cwd: "../..",
      env: {
        AI_KP_BACKUP_ROOT: ".playwright/backups",
        AI_KP_DB_PATH: ".playwright/ai-kp.sqlite3",
        AI_KP_MAP_ASSET_ROOT: ".playwright/map-assets",
        AI_KP_RULEBOOK_INDEX_ROOT: ".playwright/rag",
        PYTHONPATH: "src"
      },
      url: "http://127.0.0.1:8012/health",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "ignore",
      stderr: "pipe"
    },
    {
      name: "frontend",
      command: "pnpm dev --host 127.0.0.1 --port 5174",
      env: {
        VITE_BACKEND_TARGET: "http://127.0.0.1:8012"
      },
      url: "http://127.0.0.1:5174/play",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "ignore",
      stderr: "pipe"
    }
  ]
});
