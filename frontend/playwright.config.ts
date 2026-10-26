import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60000,
  globalTimeout: 480000,
  expect: { timeout: 15000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command:
        ".venv/bin/uvicorn --app-dir tests e2e_app:create_fixture_app --factory --host 127.0.0.1 --port 8000 --no-access-log",
      cwd: resolve(__dirname, "../backend"),
      env: { PYTHONPATH: resolve(__dirname, "../backend") },
      url: "http://127.0.0.1:8000/api/v1/health",
      reuseExistingServer: false,
      timeout: 30000,
      gracefulShutdown: { signal: "SIGINT", timeout: 5000 },
    },
    {
      command:
        "node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3000",
      url: "http://127.0.0.1:3000",
      reuseExistingServer: false,
      timeout: 30000,
      gracefulShutdown: { signal: "SIGINT", timeout: 5000 },
      env: { NEXT_TELEMETRY_DISABLED: "1" },
    },
  ],
});
