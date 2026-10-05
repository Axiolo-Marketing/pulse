import { beforeEach, describe, expect, it } from "vitest";

import type { EngagementSummary } from "./api";
import {
  clientRollupText,
  DEFAULT_CONTROLS,
  loadListControls,
  resetListControlsCache,
  saveListControls,
  sortComparator,
} from "./engagement-list";

function eng(
  name: string | null,
  recipients: number,
  completed: number,
  last: string | null = null,
): EngagementSummary {
  return {
    engagement_name: name,
    recipients_count: recipients,
    completed_recipients: completed,
    last_active_at: last,
  } as EngagementSummary;
}

describe("clientRollupText", () => {
  it("reads just the count for an empty client", () => {
    expect(clientRollupText([])).toBe("0 engagements");
  });

  it("lists non-zero status buckets in complete → in progress → waiting order", () => {
    const rows = [eng("a", 1, 0), eng("b", 2, 1), eng("c", 1, 1), eng("d", 1, 1)];
    expect(clientRollupText(rows)).toBe(
      "4 engagements · 2 complete · 1 in progress · 1 waiting",
    );
  });

  it("singularises", () => {
    expect(clientRollupText([eng("a", 1, 1)])).toBe("1 engagement · 1 complete");
  });
});

describe("sortComparator", () => {
  const names = (rows: EngagementSummary[]): (string | null)[] =>
    rows.map((r) => r.engagement_name);

  it("sorts names case-insensitively with an Untitled fallback", () => {
    const rows = [eng("banana", 0, 0), eng(null, 0, 0), eng("Apple", 0, 0)];
    expect(names(rows.sort(sortComparator("name")))).toEqual([
      "Apple",
      "banana",
      null, // "Untitled engagement" sorts after b…
    ]);
  });

  it("puts the most recently active first and never-active last", () => {
    const rows = [
      eng("old", 0, 0, "2026-01-01T00:00:00Z"),
      eng("never", 0, 0),
      eng("new", 0, 0, "2026-06-01T00:00:00Z"),
    ];
    expect(names(rows.sort(sortComparator("last_active")))).toEqual([
      "new",
      "old",
      "never",
    ]);
  });

  it("ranks by status then breaks ties by name", () => {
    const rows = [
      eng("w", 1, 0),
      eng("z-done", 1, 1),
      eng("a-done", 1, 1),
      eng("mid", 2, 1),
    ];
    expect(names(rows.sort(sortComparator("status")))).toEqual([
      "a-done",
      "z-done",
      "mid",
      "w",
    ]);
  });
});

describe("persisted list controls", () => {
  beforeEach(() => {
    sessionStorage.clear();
    resetListControlsCache();
  });

  it("defaults, then round-trips through storage", () => {
    expect(loadListControls()).toEqual(DEFAULT_CONTROLS);
    saveListControls({ ...DEFAULT_CONTROLS, status: "complete", query: "x" });
    resetListControlsCache();
    expect(loadListControls()).toMatchObject({ status: "complete", query: "x" });
  });
});
