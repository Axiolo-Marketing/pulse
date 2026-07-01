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

import { fmtSize, recipientLabel, ResponseBody, StateBadge } from "./parts";

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
