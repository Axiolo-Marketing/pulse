import { describe, expect, it } from "vitest";

import type { Card, ClientResponse, Engagement } from "./api";
import { renderCardMarkdown, type UploadInfo } from "./markdown-export";

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
