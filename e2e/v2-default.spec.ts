import { expect, test, type Page } from "@playwright/test";

// The deck (`/`) and the admin (`/admin/`) default to v2 (`<UiGate defaultV2>`,
// PR #27); `?ui=v1` is a sticky opt-out via the `pulse_ui=v1` cookie. Each
// test gets a fresh browser context, so no cookie carries over between them.
// v1 renders into `main#app` (deck) / `#admin` (admin); v2 has neither.

const TOKEN = "dec0ded0dec0ded0";

async function uiCookie(page: Page): Promise<string | undefined> {
  const cookies = await page.context().cookies();
  return cookies.find((c) => c.name === "pulse_ui")?.value;
}

test("the deck link opens v2 by default, keeping the token", async ({ page }) => {
  await page.goto(`/?t=${TOKEN}`);
  await expect(page).toHaveURL(new RegExp(`/v2/\\?t=${TOKEN}$`));
  await expect(page.locator("main#app")).toHaveCount(0);
  await expect(page.getByRole("banner")).toBeVisible();
});

test("the admin opens v2 by default, keeping the hash", async ({ page }) => {
  await page.goto("/admin/#settings");
  await expect(page).toHaveURL(/\/v2\/admin\/?#settings$/);
  await expect(page.locator("#admin")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});

test("?ui=v1 is a sticky opt-out for both pages, and ?ui=v2 undoes it", async ({
  page,
}) => {
  await page.goto(`/?ui=v1&t=${TOKEN}`);
  await expect(page).not.toHaveURL(/\/v2\//);
  await expect(page.locator("main#app")).toBeVisible();
  expect(await uiCookie(page)).toBe("v1");

  // No ?ui this time — the cookie keeps the visitor on v1, everywhere.
  await page.goto(`/?t=${TOKEN}`);
  await expect(page).not.toHaveURL(/\/v2\//);
  await expect(page.locator("main#app")).toBeVisible();
  await page.goto("/admin/");
  await expect(page).not.toHaveURL(/\/v2\//);
  await expect(page.locator("#admin")).toBeVisible();

  await page.goto(`/?ui=v2&t=${TOKEN}`);
  await expect(page).toHaveURL(/\/v2\//);
  expect(await uiCookie(page)).toBe("v2");
});
