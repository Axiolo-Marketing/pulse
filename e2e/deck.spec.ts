import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

// Critical-flow E2E for the v2 client deck: answer every response type through
// to completion, exercising save→advance, multi-select, text/link/contact,
// the voice recorder (headless, via the fake-media launch flags), and a real
// file upload. Requires the dev stack + the seeded demo deck (`make seed-deck`,
// token dec0ded0dec0ded0). global-setup.ts resets the recipient first.

const DECK = "/v2/?t=dec0ded0dec0ded0";
const csv = fileURLToPath(new URL("./fixtures/sample.csv", import.meta.url));

test("answer every card type through to 'All done'", async ({ page }) => {
  test.slow(); // voice upload + 8 cards
  await page.goto(DECK);

  // 1 — confirm-edit
  await expect(
    page.getByRole("heading", { name: "Confirm legal name" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Yes, correct" }).click();

  // 2 — single-select (auto-saves on click)
  await expect(
    page.getByRole("heading", { name: "Primary industry" }),
  ).toBeVisible();
  await page.getByRole("radio", { name: "SaaS" }).click();

  // 3 — multi-select
  await expect(
    page.getByRole("heading", { name: "Target regions" }),
  ).toBeVisible();
  await page.getByRole("checkbox", { name: "North America" }).click();
  await page.getByRole("checkbox", { name: "Europe" }).click();
  await page.getByRole("button", { name: "Continue" }).click();

  // 4 — short-text
  await expect(
    page.getByRole("heading", { name: "Billing contact name" }),
  ).toBeVisible();
  await page.getByRole("textbox").first().fill("Jane Roe");
  await page.getByRole("button", { name: "Submit" }).click();

  // 5 — long-text + a voice note (fake mic)
  await expect(
    page.getByRole("heading", { name: "Engagement objectives" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Record answer" }).click();
  await expect(page.getByText("Recording", { exact: true })).toBeVisible();
  await page.waitForTimeout(1000);
  await page.getByRole("button", { name: "Stop" }).click();
  await expect(page.getByRole("button", { name: /re-record/i })).toBeVisible({
    timeout: 20_000,
  });
  await page.getByRole("textbox").first().fill("Grow ARR and reduce churn.");
  await page.getByRole("button", { name: "Submit" }).click();

  // 6 — document-link
  await expect(
    page.getByRole("heading", { name: "Brand guidelines link" }),
  ).toBeVisible();
  await page.getByPlaceholder(/https/).fill("https://example.com/brand");
  await page.getByRole("button", { name: "Submit" }).click();

  // 7 — contact-share
  await expect(page.getByRole("heading", { name: "Project lead" })).toBeVisible();
  await page.getByPlaceholder("Name").fill("Sam Lee");
  await page.getByPlaceholder("Email").fill("sam@acme.test");
  await page.getByRole("button", { name: "Share contact" }).click();

  // 8 — file-upload
  await expect(page.getByRole("heading", { name: "Signed SOW" })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles(csv);
  await expect(page.getByText("sample.csv")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Continue" }).click();

  // done
  await expect(page.getByTestId("deck-complete")).toBeVisible();
  await expect(page.getByText(/all done/i)).toBeVisible();
});
