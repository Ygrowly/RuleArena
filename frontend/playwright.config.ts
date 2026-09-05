import { defineConfig } from "@playwright/test";

// Uses the system-installed browser (no Playwright browser download needed).
// Override with E2E_CHANNEL=msedge if Chrome is unavailable.
const channel = process.env.E2E_CHANNEL ?? "chrome";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:8080",
    channel,
  },
  retries: 0,
});
