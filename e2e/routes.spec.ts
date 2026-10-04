import { expect, test, type Page } from "@playwright/test";

// URL scheme after the v1 UI was removed: the canonical pages serve the React
// app directly (no redirect), and the retired `/v2/*` URLs — still in some
// visitors' history/bookmarks from the side-by-side period — forward to them,
// keeping the query string and hash. A leftover `?ui=v1` / `pulse_ui` cookie
// is ignored.

const TOKEN = "dec0ded0dec0ded0";

// The deck rendered for the token: a card (banner chrome) or, once the deck
// spec has finished the demo deck, the completion screen. Never the
// bad-token / error states.
async function expectDeckLoaded(page: Page): Promise<void> {
  await expect(
    page.getByRole("banner").or(page.getByRole("heading", { name: /All done/ })),
  ).toBeVisible();
  await expect(page.getByText(/could not find your engagement/i)).toHaveCount(0);
}

test("the deck link serves the deck at /, keeping the token", async ({ page }) => {
  await page.goto(`/?t=${TOKEN}`);
  await expect(page).toHaveURL(new RegExp(`/\\?t=${TOKEN}$`));
  await expectDeckLoaded(page);
});

test("the admin is served at /admin/", async ({ page }) => {
  await page.goto("/admin/#settings");
  await expect(page).toHaveURL(/\/admin\/#settings$/);
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});

test("a leftover ?ui=v1 opt-out no longer changes anything", async ({ page, context, baseURL }) => {
  await context.addCookies([
    { name: "pulse_ui", value: "v1", url: baseURL ?? "http://localhost:14321" },
  ]);
  await page.goto(`/?ui=v1&t=${TOKEN}`);
  await expect(page).not.toHaveURL(/\/v2\//);
  await expectDeckLoaded(page);
});

for (const [legacy, canonical] of [
  [`/v2/?t=${TOKEN}`, new RegExp(`/\\?t=${TOKEN}$`)],
  ["/v2/admin/#settings/activity", /\/admin\/#settings\/activity$/],
  ["/v2/invite?token=bogus", /\/invite\?token=bogus$/],
  ["/v2/unsubscribe?u=bogus", /\/unsubscribe\?u=bogus$/],
  ["/v2/preview?e=x", /\/preview\?e=x$/],
] as const) {
  test(`legacy ${legacy} redirects to the canonical page`, async ({ page }) => {
    await page.goto(legacy);
    await expect(page).toHaveURL(canonical);
    await expect(page).not.toHaveURL(/\/v2\//);
  });
}
