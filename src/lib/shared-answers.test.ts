import { describe, expect, it } from "vitest";

import type { AnswerEvent, Card, EngagementDetail } from "./api";
import { describeChange, sharedExportDetail } from "./shared-answers";

const grid = {
  id: "g",
  response_type: "grid",
  block_config: {
    rows: [{ key: "crm", label: "CRM" }],
    columns: [
      { key: "does", label: "Does the work", options: null },
      { key: "staff", label: "Staffing", options: ["Open seat"] },
    ],
  },
} as unknown as Card;

function ev(change: AnswerEvent["change"]): AnswerEvent {
  return {
    id: "e",
    card_id: "g",
    recipient_id: null,
    actor_label: "Chachi",
    change,
    created_at: "2026-10-26T14:00:00Z",
  };
}

describe("describeChange", () => {
  it("names the grid cells that changed", () => {
    expect(describeChange(grid, ev({ cells: { crm: { does: "Pat", staff: "" } } }))).toEqual([
      "CRM · Does the work: Pat",
      "CRM · Staffing: cleared",
    ]);
  });

  it("quotes text answers and notes clears", () => {
    const q = { ...grid, response_type: "long-text" } as Card;
    expect(describeChange(q, ev({ text: "$15k" }))).toEqual(["“$15k”"]);
    expect(describeChange(q, ev({ text: "" }))).toEqual(["Cleared the answer"]);
  });
});

describe("sharedExportDetail", () => {
  it("turns shared answers into one respondent's answers", () => {
    const detail = {
      engagement: { id: "e1", created_at: "2026-10-01T00:00:00Z" },
      cards: [grid],
      recipients: [{ id: "r1" }],
      responses: [],
      uploads: [],
      shared_answers: [
        { card_id: "g", value: { cells: {} }, updated_at: "2026-10-26T14:00:00Z", updated_by_name: "Adam" },
      ],
    } as unknown as EngagementDetail;
    const out = sharedExportDetail(detail);
    expect(out.recipients.map((r) => r.name)).toEqual(["Shared answers"]);
    expect(out.responses).toHaveLength(1);
    expect(out.responses[0]).toMatchObject({ card_id: "g", state: "answered", recipient_id: "shared" });
  });
});
