import { execSync } from "node:child_process";
import { expect, test, type Page } from "@playwright/test";

// E2E for the two ways an operator updates their password:
//
//   1. Signed in — Settings → Personal → "Change password".
//   2. Locked out — "Forgot password?" → emailed reset link → new password.
//
// Flow 2 exists because the reset link once pointed at a standalone
// /reset-password page that doesn't exist (the admin shell serves the flow at
// /admin/?reset-password-token=…), so every emailed link 404'd. The test
// drives the REAL emailed link: in dev the backend logs outbound mail
// (email(log-only)…), and we pull the link out of `docker compose logs` —
// same docker-exec pattern as global-setup.ts.
//
// Requires the dev stack (`make dev`) + the seeded dev admin (`make
// seed-dev`, dev@example.com / dev-admin-password). Setup/teardown re-run the
// seeder so the password is back to the default even if a run dies mid-test.

const EMAIL = "dev@example.com";
const DEFAULT_PW = "dev-admin-password";
const TEMP_PW_SETTINGS = "e2e-temp-password-1";
const TEMP_PW_RESET = "e2e-temp-password-2";

// The admin console is desktop chrome; override the Pixel 7 project viewport.
test.use({ viewport: { width: 1280, height: 900 } });
test.describe.configure({ mode: "serial" });

function reseedDevAdmin(): void {
  try {
    execSync(
      "docker compose exec -T backend uv run python -m scripts.dev_seed",
      { stdio: ["ignore", "ignore", "inherit"] },
    );
  } catch {
    console.warn(
      "[e2e] could not reseed the dev admin (is the backend container up?); password tests may fail.",
    );
  }
}

test.beforeAll(reseedDevAdmin);
test.afterAll(reseedDevAdmin); // restore dev-admin-password no matter what

async function login(page: Page, password: string): Promise<void> {
  await page.goto("/admin/");
  await page.locator("#login-email").fill(EMAIL);
  await page.locator("#login-pw").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  // The user-menu trigger only renders in the authed shell.
  await expect(page.locator("#user-menu-trigger")).toBeVisible();
}

async function changePasswordViaSettings(
  page: Page,
  current: string,
  next: string,
): Promise<void> {
  await page.locator("#user-menu-trigger").click();
  await page.locator("#nav-settings").click();
  await expect(
    page.getByRole("heading", { name: "Change password" }),
  ).toBeVisible();
  await page.locator("#pw-current").fill(current);
  await page.locator("#pw-new").fill(next);
  await page.locator("#pw-confirm").fill(next);
  await page.getByRole("button", { name: "Update password" }).click();
  await expect(page.locator(".toast")).toHaveText("Password updated");
}

test("operator changes their password from Settings and signs back in", async ({
  page,
}) => {
  await login(page, DEFAULT_PW);
  await changePasswordViaSettings(page, DEFAULT_PW, TEMP_PW_SETTINGS);

  // Sign out, sign back in with the NEW password.
  await page.locator("#user-menu-trigger").click();
  await page.locator("#logout").click();
  await expect(page.locator("#login-form")).toBeVisible();

  await login(page, TEMP_PW_SETTINGS);

  // Restore the default through the same form (also proves a second
  // consecutive change works against the freshly-minted session).
  await changePasswordViaSettings(page, TEMP_PW_SETTINGS, DEFAULT_PW);
});

test("forgot-password: the emailed reset link lands on a working form", async ({
  page,
}) => {
  // Request the reset through the real UI.
  await page.goto("/admin/");
  await page.locator("[data-action='forgot']").click();
  await page.locator("#forgot-email").fill(EMAIL);
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(
    page.getByRole("heading", { name: "Check your email" }),
  ).toBeVisible();

  // Dev mode logs the outbound email instead of sending it. The dev log
  // handler wraps long lines with padding, so strip ALL whitespace before
  // extracting the token (base64url + dots; the literal \n after it in the
  // repr'd body is the terminator).
  let token: string | null = null;
  await expect
    .poll(
      () => {
        // --no-log-prefix matters: the "backend-1 |" prefix on wrapped
        // continuation lines would otherwise get glued into the token.
        const raw = execSync(
          "docker compose logs backend --since 90s --no-log-prefix",
          { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
        );
        const flat = raw
          .slice(raw.lastIndexOf("email(log-only)"))
          .replace(/\s+/g, "");
        token =
          /reset-password-token=([A-Za-z0-9._-]+)/.exec(flat)?.[1] ?? null;
        return token;
      },
      { message: "reset link never appeared in the backend logs" },
    )
    .not.toBeNull();

  // THE regression: the emailed link must render the reset form, not a 404.
  await page.goto(`/admin/?reset-password-token=${token}`);
  await expect(
    page.getByRole("heading", { name: "Set new password" }),
  ).toBeVisible();

  await page.locator("#reset-pw").fill(TEMP_PW_RESET);
  await page.getByRole("button", { name: "Set password" }).click();
  await expect(
    page.getByRole("heading", { name: "Password updated" }),
  ).toBeVisible();

  // And the new password actually signs in.
  await page.getByRole("link", { name: "Sign in" }).click();
  await expect(page.locator("#login-form")).toBeVisible();
  await login(page, TEMP_PW_RESET);
});
