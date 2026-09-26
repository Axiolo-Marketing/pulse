import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Card as CardModel, ClientResponse } from "@/lib/api";

import { DeckOutline } from "./DeckOutline";
import { MultiSelectInput, SingleSelectInput } from "./inputs";

function makeCard(over: Partial<CardModel> = {}): CardModel {
  return {
    id: "c1",
    engagement_id: "e1",
    order_index: 1,
    category: "Cat",
    title: "Title",
    context: "Context",
    question: "The question?",
    response_type: "single-select",
    options: ["Alpha", "Beta", "Gamma"],
    default_value: null,
    skip_allowed: false,
    attachment_path: null,
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

/** Pretend the device has (or lacks) a mouse + keyboard. */
function mockPointer(fine: boolean): void {
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({
        matches: query.includes("pointer: fine") ? fine : false,
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      }) as unknown as MediaQueryList,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("number-key shortcuts", () => {
  it("picks the matching option on a keyboard device", async () => {
    mockPointer(true);
    const onSelect = vi.fn();
    render(
      <SingleSelectInput
        card={makeCard()}
        saving={false}
        onSelect={onSelect}
        onNoteSubmit={vi.fn()}
        onSkip={vi.fn()}
      />,
    );
    // Hint badges are shown, but hidden from the options' accessible names.
    expect(screen.getByRole("radio", { name: "Beta" })).toBeInTheDocument();
    await userEvent.keyboard("2");
    expect(onSelect).toHaveBeenCalledWith("Beta", undefined);
  });

  it("does nothing on touch devices", async () => {
    mockPointer(false);
    const onSelect = vi.fn();
    render(
      <SingleSelectInput
        card={makeCard()}
        saving={false}
        onSelect={onSelect}
        onNoteSubmit={vi.fn()}
        onSkip={vi.fn()}
      />,
    );
    await userEvent.keyboard("2");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("ignores digits typed into the notes box", async () => {
    mockPointer(true);
    const onSelect = vi.fn();
    render(
      <SingleSelectInput
        card={makeCard()}
        saving={false}
        onSelect={onSelect}
        onNoteSubmit={vi.fn()}
        onSkip={vi.fn()}
      />,
    );
    await userEvent.type(screen.getByLabelText(/notes/i), "2 offices");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("toggles options on multi-select", async () => {
    mockPointer(true);
    const onSubmit = vi.fn();
    render(
      <MultiSelectInput
        card={makeCard({ response_type: "multi-select" })}
        saving={false}
        onSubmit={onSubmit}
        onSkip={vi.fn()}
      />,
    );
    await userEvent.keyboard("1");
    await userEvent.keyboard("3");
    expect(screen.getByRole("checkbox", { name: "Alpha" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("checkbox", { name: "Gamma" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(onSubmit).toHaveBeenCalledWith(["Alpha", "Gamma"], undefined);
  });
});

describe("DeckOutline", () => {
  const cards = [
    makeCard({ id: "a", title: "Legal name", category: "Company" }),
    makeCard({ id: "b", title: "Industry", category: "Company" }),
    makeCard({ id: "c", title: "Regions", category: "Scope" }),
  ];

  it("groups by section, marks progress, and jumps on click", async () => {
    const onJump = vi.fn();
    const responses = new Map<string, ClientResponse>([
      ["a", { state: "answered" } as ClientResponse],
    ]);
    render(
      <DeckOutline
        cards={cards}
        responses={responses}
        currentIndex={1}
        onJump={onJump}
      />,
    );
    expect(screen.getByRole("heading", { name: "Company" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Scope" })).toBeInTheDocument();
    expect(screen.getByText("1 of 3 answered")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Industry/ }),
    ).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("button", { name: /Legal name.*answered/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Regions/ }));
    expect(onJump).toHaveBeenCalledWith(2);
  });
});
