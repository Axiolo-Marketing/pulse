import { describe, expect, it } from "vitest";

import type { Card } from "./api";
import { deckUrl } from "./clipboard";
import { gridMarkdown, gridTable } from "./grid";

const card = {
  response_type: "grid",
  block_config: {
    rows: [
      { key: "lead-response", label: "Lead response" },
      { key: "crm", label: "CRM" },
    ],
    columns: [
      { key: "does", label: "Does the work", options: null },
      { key: "staff", label: "Staffing", options: ["Your team", "Open seat"] },
    ],
  },
} as unknown as Card;

describe("gridTable / gridMarkdown", () => {
  it("lays cells out against the card's rows and columns", () => {
    const t = gridTable(card, { cells: { crm: { staff: "Open seat", does: "Pat | Sam" } } });
    expect(t.filled).toBe(2);
    expect(t.rows[1]).toEqual({ label: "CRM", cells: ["Pat | Sam", "Open seat"] });
    expect(gridMarkdown(t)).toBe(
      [
        "| | Does the work | Staffing |",
        "|---|---|---|",
        "| **Lead response** | — | — |",
        "| **CRM** | Pat \\| Sam | Open seat |",
      ].join("\n"),
    );
  });

  it("says when nothing is filled in", () => {
    expect(gridMarkdown(gridTable(card, null))).toBe("_Nothing filled in yet._");
  });
});

describe("deckUrl", () => {
  it("links to the page when the engagement has one", () => {
    expect(deckUrl("abc", "https://doc.example.com/#top")).toBe("https://doc.example.com/?t=abc");
    expect(deckUrl("abc", "https://doc.example.com/p?x=1")).toBe(
      "https://doc.example.com/p?x=1&t=abc",
    );
    expect(deckUrl("abc")).toBe(`${window.location.origin}/?t=abc`);
  });
});
