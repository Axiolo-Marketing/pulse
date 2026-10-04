import { expect, test } from "@playwright/test";

// Smoke coverage for the invite + unsubscribe islands. Asserts flow/structure
// (roles + text + testids), NOT pixels. Needs the full dev stack (frontend +
// backend) running; see playwright.config.ts.

test("unsubscribe renders a result card", async ({ page }) => {
  await page.goto("/unsubscribe?u=bogus");
  await expect(page.getByTestId("unsubscribe-card")).toBeVisible();
  await expect(page.getByTestId("unsubscribe-title")).toBeVisible();
});

test("invite shows the no-token state", async ({ page }) => {
  await page.goto("/invite");
  await expect(
    page.getByRole("heading", { name: "No invite token" }),
  ).toBeVisible();
});

test("invite resolves a bogus token to a terminal card", async ({ page }) => {
  await page.goto("/invite?token=bogus");
  await expect(page.getByTestId("invite-title")).toBeVisible();
});
