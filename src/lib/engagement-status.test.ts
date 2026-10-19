import { describe, expect, it } from "vitest";

import { engagementStatus } from "./engagement-status";

describe("engagementStatus", () => {
  it("is waiting with no recipients or no answers", () => {
    expect(engagementStatus({ recipients_count: 0, completed_recipients: 0 })).toBe("waiting");
    expect(
      engagementStatus({ recipients_count: 1, completed_recipients: 0, answered_responses: 0 }),
    ).toBe("waiting");
  });

  it("is in progress once anyone has answered, even if no one has finished", () => {
    expect(
      engagementStatus({ recipients_count: 1, completed_recipients: 0, answered_responses: 20 }),
    ).toBe("in_progress");
  });

  it("is in progress when some but not all respondents are done", () => {
    expect(engagementStatus({ recipients_count: 2, completed_recipients: 1 })).toBe(
      "in_progress",
    );
  });

  it("is complete when every respondent is done", () => {
    expect(
      engagementStatus({ recipients_count: 2, completed_recipients: 2, answered_responses: 30 }),
    ).toBe("complete");
  });
});
