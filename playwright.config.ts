import { defineConfig, devices } from "@playwright/test";

// E2E smoke tests for the v2 islands + the v1→v2 opt-in gate.
//
// Requires: the dev stack running (`make dev`) at localhost:14321, and
// Playwright browsers installed (`npx playwright install chromium`).
//
// NOT part of the pre-push gate (`make front-check`): Playwright browsers need
// glibc, which the alpine dev container lacks — run these from the host or a
// Debian-based environment. Override the target with PULSE_E2E_BASE_URL.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: "list",
  // Resets the demo recipient's answers so the deck spec starts fresh (needs
  // the dev DB container running). Skip with PULSE_E2E_NO_RESET=1.
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: process.env.PULSE_E2E_BASE_URL ?? "http://localhost:14321",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "mobile-chrome",
      use: {
        ...devices["Pixel 7"],
        // Auto-grant the mic + feed a synthetic audio track so the voice
        // recorder flow runs headless.
        launchOptions: {
          args: [
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
          ],
        },
      },
    },
  ],
});
