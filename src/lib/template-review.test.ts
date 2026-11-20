import { describe, expect, it } from "vitest";

import { shouldShowReviewBanner, type ReviewBannerInput } from "./template-review";

const base: ReviewBannerInput = {
  template_id: "t1",
  cardCount: 5,
  recipients: [{ invited_at: null }],
  answeredCount: 0,
  dismissed: false,
};

describe("shouldShowReviewBanner", () => {
  it("shows for a fresh templated deck", () => {
    expect(shouldShowReviewBanner(base)).toBe(true);
    expect(shouldShowReviewBanner({ ...base, recipients: [] })).toBe(true);
  });

  it.each([
    ["no template", { template_id: null }],
    ["no cards", { cardCount: 0 }],
    ["someone invited", { recipients: [{ invited_at: null }, { invited_at: "2026-01-01" }] }],
    ["an answer exists", { answeredCount: 1 }],
    ["dismissed", { dismissed: true }],
  ])("hides with %s", (_, patch) => {
    expect(shouldShowReviewBanner({ ...base, ...patch })).toBe(false);
  });
});
