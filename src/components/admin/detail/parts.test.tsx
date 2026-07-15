import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  Card as CardModel,
  ClientResponse,
  Recipient,
  UploadRow,
} from "@/lib/api";

// parts.tsx calls adminApi.uploadDownloadUrl for file/voice sources.
vi.mock("@/lib/api", () => ({
  adminApi: { uploadDownloadUrl: (id: string) => `/dl/${id}` },
}));

import {
  AiFollowupBadge,
  AiFollowupCountBadge,
  fmtSize,
  recipientLabel,
  ResponseBody,
  StateBadge,
} from "./parts";

function makeCard(over: Partial<CardModel> = {}): CardModel {
  return {
    id: "c1",
    engagement_id: "e1",
    order_index: 1,
    category: "Cat",
    title: "Title",
    context: "Context",
    question: "Q?",
    response_type: "short-text",
    options: null,
    default_value: null,
    skip_allowed: false,
    attachment_path: null,
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function answered(response_value: unknown, state = "answered"): ClientResponse {
  return { response_value, state } as ClientResponse;
}

function upload(over: Partial<UploadRow>): UploadRow {
  return {
    id: "u1",
    kind: "file",
    file_name: "doc.pdf",
    file_size_bytes: 2048,
    ...over,
  } as UploadRow;
}

describe("ResponseBody", () => {
  it("confirm-edit confirmed vs edited", () => {
    const card = makeCard({ response_type: "confirm-edit" });
    const { rerender } = render(
      <ResponseBody card={card} response={answered({ confirmed: true })} uploads={[]} />,
    );
    expect(screen.getByText("Confirmed as written.")).toBeInTheDocument();

    rerender(
      <ResponseBody
        card={card}
        response={answered({ confirmed: false, correction: "New legal name" })}
        uploads={[]}
      />,
    );
    expect(screen.getByText("New legal name")).toBeInTheDocument();
  });

  it("single-select shows the choice", () => {
    render(
      <ResponseBody
        card={makeCard({ response_type: "single-select" })}
        response={answered({ selected: "SaaS" })}
        uploads={[]}
      />,
    );
    expect(screen.getByText("SaaS")).toBeInTheDocument();
  });

  it("multi-select lists choices, empty falls back", () => {
    const card = makeCard({ response_type: "multi-select" });
    const { rerender } = render(
      <ResponseBody
        card={card}
        response={answered({ selected: ["NA", "EU"] })}
        uploads={[]}
      />,
    );
    expect(screen.getByText("NA")).toBeInTheDocument();
    expect(screen.getByText("EU")).toBeInTheDocument();

    rerender(
      <ResponseBody card={card} response={answered({ selected: [] })} uploads={[]} />,
    );
    expect(screen.getByText("None selected.")).toBeInTheDocument();
  });

  it("document-link renders an anchor to the url", () => {
    render(
      <ResponseBody
        card={makeCard({ response_type: "document-link" })}
        response={answered({ url: "https://ex.com/x" })}
        uploads={[]}
      />,
    );
    const link = screen.getByRole("link", { name: "https://ex.com/x" });
    expect(link).toHaveAttribute("href", "https://ex.com/x");
  });

  it("contact-share shows name, role, email", () => {
    render(
      <ResponseBody
        card={makeCard({ response_type: "contact-share" })}
        response={answered({ name: "Sam", role: "PM", email: "s@x.io" })}
        uploads={[]}
      />,
    );
    expect(screen.getByText("Sam (PM)")).toBeInTheDocument();
    expect(screen.getByText("s@x.io")).toBeInTheDocument();
  });

  it("file-upload links each file through the admin download url", () => {
    render(
      <ResponseBody
        card={makeCard({ response_type: "file-upload" })}
        response={answered({ file_ids: ["f1"] })}
        uploads={[upload({ id: "f1", kind: "file", file_name: "sow.csv" })]}
      />,
    );
    const link = screen.getByRole("link", { name: "sow.csv" });
    expect(link).toHaveAttribute("href", "/dl/f1");
  });

  it("renders a voice player and the note regardless of type", () => {
    const { container } = render(
      <ResponseBody
        card={makeCard({ response_type: "short-text" })}
        response={answered({ text: "Hi", note: "extra context" })}
        uploads={[upload({ id: "v1", kind: "voice", file_name: "voice.webm" })]}
      />,
    );
    expect(screen.getByText("Hi")).toBeInTheDocument();
    expect(screen.getByText("extra context")).toBeInTheDocument();
    const audio = container.querySelector("audio");
    expect(audio).toHaveAttribute("src", "/dl/v1");
  });

  it("skipped and not-started states", () => {
    const card = makeCard();
    const { rerender } = render(
      <ResponseBody card={card} response={answered(null, "skipped")} uploads={[]} />,
    );
    expect(screen.getByText("Skipped.")).toBeInTheDocument();

    rerender(<ResponseBody card={card} response={undefined} uploads={[]} />);
    expect(screen.getByText("Not yet viewed.")).toBeInTheDocument();
  });
});

describe("StateBadge", () => {
  it("maps each state to a label, unknown → Not viewed", () => {
    const { rerender } = render(<StateBadge response={answered({}, "answered")} />);
    expect(screen.getByText("Answered")).toBeInTheDocument();
    rerender(<StateBadge response={answered({}, "viewed")} />);
    expect(screen.getByText("Viewed")).toBeInTheDocument();
    rerender(<StateBadge response={undefined} />);
    expect(screen.getByText("Not viewed")).toBeInTheDocument();
  });
});

describe("helpers", () => {
  it("recipientLabel prefers email then name", () => {
    expect(recipientLabel({ email: "a@b.c", name: "A" } as Recipient)).toBe("a@b.c");
    expect(recipientLabel({ email: "", name: "A" } as Recipient)).toBe("A");
    expect(recipientLabel({ email: "", name: "" } as Recipient)).toBe("Respondent");
  });

  it("fmtSize formats bytes/KB/MB", () => {
    expect(fmtSize(500)).toBe("500 B");
    expect(fmtSize(2048)).toBe("2 KB");
    expect(fmtSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

function recipient(over: Partial<Recipient> = {}): Recipient {
  return {
    id: "r1",
    engagement_id: "e1",
    email: "renee@example.com",
    name: "Renee",
    token: "0123456789abcdef",
    last_active_at: null,
    invited_at: null,
    last_reminded_at: null,
    reminder_count: 0,
    unsubscribed_at: null,
    created_at: "2026-01-01T00:00:00Z",
    completed_count: 0,
    total_cards: 0,
    ...over,
  };
}

describe("AiFollowupBadge", () => {
  it("renders nothing for an operator-authored card (source omitted or explicit)", () => {
    const { container, rerender } = render(
      <AiFollowupBadge
        card={makeCard()}
        recipients={[]}
        cards={[]}
        responses={[]}
      />,
    );
    expect(container).toBeEmptyDOMElement();

    rerender(
      <AiFollowupBadge
        card={makeCard({ source: "operator" })}
        recipients={[]}
        cards={[]}
        responses={[]}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the AI badge with recipient attribution when the recipient resolves", () => {
    render(
      <AiFollowupBadge
        card={makeCard({ source: "ai", recipient_id: "r1" })}
        recipients={[recipient({ id: "r1", email: "renee@example.com" })]}
        cards={[]}
        responses={[]}
      />,
    );
    expect(screen.getByText("AI follow-up · for renee@example.com")).toBeInTheDocument();
  });

  it("renders the bare badge when the recipient can't be resolved", () => {
    render(
      <AiFollowupBadge
        card={makeCard({ source: "ai", recipient_id: "missing" })}
        recipients={[]}
        cards={[]}
        responses={[]}
      />,
    );
    expect(screen.getByText("AI follow-up")).toBeInTheDocument();
  });

  it("tooltip resolves the triggering card's title when the chain is resolvable", () => {
    const parent = makeCard({ id: "parent1", title: "Confirm your legal name" });
    const triggerResponse = { id: "resp1", card_id: "parent1" } as ClientResponse;

    render(
      <AiFollowupBadge
        card={makeCard({
          id: "ai1",
          source: "ai",
          recipient_id: "r1",
          generated_from_response_id: "resp1",
        })}
        recipients={[recipient({ id: "r1" })]}
        cards={[parent]}
        responses={[triggerResponse]}
      />,
    );
    const badge = screen.getByText("AI follow-up · for renee@example.com");
    expect(badge.closest("[title]")).toHaveAttribute(
      "title",
      'Generated from a correction on "Confirm your legal name"',
    );
  });

  it("tooltip falls back to a generic label when the trigger chain can't be resolved", () => {
    render(
      <AiFollowupBadge
        card={makeCard({ source: "ai", recipient_id: "r1" })}
        recipients={[recipient({ id: "r1" })]}
        cards={[]}
        responses={[]}
      />,
    );
    const badge = screen.getByText("AI follow-up · for renee@example.com");
    expect(badge.closest("[title]")).toHaveAttribute(
      "title",
      "AI-generated follow-up card",
    );
  });

  it("escapes a hostile card title in the tooltip instead of rendering it as HTML", () => {
    const hostileTitle = '<img src=x onerror="window.__pwned=true">';
    const parent = makeCard({ id: "parent1", title: hostileTitle });
    const triggerResponse = { id: "resp1", card_id: "parent1" } as ClientResponse;

    render(
      <AiFollowupBadge
        card={makeCard({
          id: "ai1",
          source: "ai",
          recipient_id: "r1",
          generated_from_response_id: "resp1",
        })}
        recipients={[recipient({ id: "r1" })]}
        cards={[parent]}
        responses={[triggerResponse]}
      />,
    );
    // The hostile string must land verbatim in the `title` attribute (plain
    // text, never interpreted as markup) — and no such element must have
    // actually been injected into the DOM.
    const badge = screen.getByText("AI follow-up · for renee@example.com");
    expect(badge.closest("[title]")).toHaveAttribute(
      "title",
      `Generated from a correction on "${hostileTitle}"`,
    );
    expect(document.querySelector('img[src="x"]')).toBeNull();
    expect(
      (window as unknown as { __pwned?: boolean }).__pwned,
    ).toBeUndefined();
  });
});

describe("AiFollowupCountBadge", () => {
  it("renders nothing when count is 0", () => {
    const { container } = render(<AiFollowupCountBadge count={0} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders +N with a singular label at count 1", () => {
    render(<AiFollowupCountBadge count={1} />);
    const badge = screen.getByText("+1");
    expect(badge).toHaveAttribute("title", "1 AI follow-up question added");
    expect(badge).toHaveAttribute(
      "aria-label",
      "1 AI follow-up question added",
    );
  });

  it("renders +N with a plural label when count > 1", () => {
    render(<AiFollowupCountBadge count={3} />);
    const badge = screen.getByText("+3");
    expect(badge).toHaveAttribute(
      "title",
      "3 AI follow-up questions added",
    );
  });
});
