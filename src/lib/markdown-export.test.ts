import { describe, expect, it } from "vitest";

import type {
  Card,
  ClientResponse,
  Engagement,
  EngagementDetail,
  Recipient,
} from "./api";
import {
  buildCardMarkdown,
  buildEngagementExport,
  exportFilename,
  renderCardMarkdown,
  slugify,
  type UploadInfo,
} from "./markdown-export";

const card = {
  id: "c1",
  title: "Engagement objectives",
  context: "Why we're asking.",
  question: "What does success look like?",
  response_type: "long-text",
} as Card;

const client = { name: "Dev Demo Co" } as Engagement;

const response = {
  state: "answered",
  response_value: { text: "Grow ARR." },
} as ClientResponse;

function render(uploads: UploadInfo[]): string {
  return renderCardMarkdown({ card, client, response, uploads });
}

describe("voice answers in the Markdown export", () => {
  const voice: UploadInfo = {
    id: "u1",
    name: "voice.webm",
    sizeBytes: 2048,
    url: "https://pulse.example/api/admin/uploads/u1/download",
    kind: "voice",
  };

  it("links the recording", () => {
    expect(render([voice])).toContain(
      "**Voice answer:** [voice.webm](https://pulse.example/api/admin/uploads/u1/download)",
    );
  });

  it("quotes the transcript under the recording when there is one", () => {
    const md = render([{ ...voice, transcript: "First line.\nSecond line." }]);
    expect(md).toContain("**Transcript:**\n> First line.\n> Second line.");
  });

  it("omits the transcript block when there's no transcript", () => {
    expect(render([{ ...voice, transcript: null }])).not.toContain("Transcript");
    expect(render([{ ...voice, transcript: "   " }])).not.toContain("Transcript");
  });
});

describe("engagement export", () => {
  const cards = [
    { ...card, id: "c2", title: "Second", order_index: 2 },
    { ...card, id: "c1", title: "First", order_index: 1 },
  ] as Card[];
  const rec = { id: "r1", email: "a@x.com", name: "Ann" } as Recipient;
  const url = (id: string): string => `/dl/${id}`;
  const detail = (recipients: Recipient[]): EngagementDetail =>
    ({
      engagement: client,
      recipients,
      cards,
      responses: [
        { recipient_id: "r1", card_id: "c1", state: "answered", response_value: { text: "Hi" } },
      ],
      uploads: [],
    }) as unknown as EngagementDetail;

  it("falls back to the deck's cards when there are no respondents", () => {
    const md = buildEngagementExport(detail([]), url);
    expect(md).not.toBe("");
    expect(md.indexOf("# First")).toBeLessThan(md.indexOf("# Second"));
    expect(md).toContain("_Not yet viewed._");
    expect(md).toContain("## Response from Dev Demo Co\n");
  });

  it("exports each respondent's answers, labelled", () => {
    const md = buildEngagementExport(detail([rec]), url);
    expect(md).toContain("## Response from Dev Demo Co — a@x.com");
    expect(md).toContain("Hi");
  });

  it("builds a single answer block for one respondent", () => {
    const md = buildCardMarkdown(detail([rec]), cards[1]!, rec, url);
    expect(md).toContain("# First");
    expect(md).toContain("Hi");
    expect(md).not.toContain("# Second");
  });
});

describe("export filename", () => {
  it("slugifies to clean ASCII", () => {
    expect(slugify("  Café — Q3 Brand Refresh! ")).toBe("cafe-q3-brand-refresh");
    expect(slugify("***")).toBe("");
  });

  it("uses the engagement name, else the client name, plus the date", () => {
    const d = new Date("2026-10-04T12:00:00Z");
    expect(exportFilename({ engagement_name: "Q3 Brand", name: "Acme" }, d)).toBe(
      "q3-brand-2026-10-04.md",
    );
    expect(exportFilename({ engagement_name: " ", name: "Acme Co" }, d)).toBe(
      "acme-co-2026-10-04.md",
    );
    expect(exportFilename({ engagement_name: null, name: "***" }, d)).toBe(
      "engagement-2026-10-04.md",
    );
  });
});
