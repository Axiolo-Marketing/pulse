import { expect, test } from "@playwright/test";

// Smoke coverage for the v2 islands + the v1→v2 opt-in gate. Asserts
// flow/structure (roles + text + testids), NOT pixels — v2 is a redesign, so
// the bar is behavioral parity, not a visual match. Needs the full dev stack
// (frontend + backend) running; see playwright.config.ts.

test("v2 unsubscribe island renders a result card", async ({ page }) => {
  await page.goto("/v2/unsubscribe?u=bogus");
  await expect(page.getByTestId("unsubscribe-card")).toBeVisible();
  await expect(page.getByTestId("unsubscribe-title")).toBeVisible();
});

test("v2 invite shows the no-token state", async ({ page }) => {
  await page.goto("/v2/invite");
  await expect(
    page.getByRole("heading", { name: "No invite token" }),
  ).toBeVisible();
});

test("v1 invite redirects to v2 when ?ui=v2 (and resolves the token)", async ({
  page,
}) => {
  await page.goto("/invite?ui=v2&token=bogus");
  await expect(page).toHaveURL(/\/v2\/invite/);
  // Bogus token resolves to a terminal card via the backend.
  await expect(page.getByTestId("invite-title")).toBeVisible();
});

test("v1 stays on v1 by default (no opt-in cookie)", async ({ page }) => {
  await page.goto("/unsubscribe?u=bogus");
  await expect(page).toHaveURL(/\/unsubscribe(\?|$)/);
  await expect(page).not.toHaveURL(/\/v2\//);
});
