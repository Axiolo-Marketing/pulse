import { describe, expect, it } from "vitest";

import { formatInviteCount, formatMemberCount } from "./OrganizationTab";

describe("org count formatters", () => {
  it("pluralizes members", () => {
    expect(formatMemberCount(1)).toBe("1 member");
    expect(formatMemberCount(3)).toBe("3 members");
  });
  it("pluralizes pending invites", () => {
    expect(formatInviteCount(0)).toBe("no pending invites");
    expect(formatInviteCount(1)).toBe("1 pending invite");
    expect(formatInviteCount(2)).toBe("2 pending invites");
  });
});
