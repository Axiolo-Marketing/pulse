import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Card as CardModel, ClientResponse } from "@/lib/api";
import {
  ConfirmEditView,
  ContactShareInput,
  DocumentLinkInput,
  EditBody,
  MultiSelectInput,
  SingleSelectInput,
  TextInput,
} from "./inputs";

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
    options: null,
    default_value: null,
    skip_allowed: false,
    attachment_path: null,
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function priorResponse(response_value: unknown, state = "answered"): ClientResponse {
  return { response_value, state } as ClientResponse;
}

describe("SingleSelectInput", () => {
  const card = makeCard({
    response_type: "single-select",
    options: ["A", "B", "C"],
    skip_allowed: true,
  });

  it("auto-saves the clicked option (with the typed note)", async () => {
    const onSelect = vi.fn();
    render(
      <SingleSelectInput
        card={card}
        saving={false}
        onSelect={onSelect}
        onSkip={vi.fn()}
      />,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/notes/i), "  because  ");
    await user.click(screen.getByRole("radio", { name: "B" }));
    expect(onSelect).toHaveBeenCalledWith("B", "because");
  });

  it("marks the prior selection as checked", () => {
    render(
      <SingleSelectInput
        card={card}
        saving={false}
        existing={priorResponse({ selected: "C" })}
        onSelect={vi.fn()}
        onSkip={vi.fn()}
      />,
    );
    expect(screen.getByRole("radio", { name: "C" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});

describe("MultiSelectInput", () => {
  const card = makeCard({
    response_type: "multi-select",
    options: ["X", "Y", "Z"],
  });

  it("submits the toggled set", async () => {
    const onSubmit = vi.fn();
    render(
      <MultiSelectInput
        card={card}
        saving={false}
        onSubmit={onSubmit}
        onSkip={vi.fn()}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "X" }));
    await user.click(screen.getByRole("checkbox", { name: "Z" }));
    await user.click(screen.getByRole("button", { name: /continue/i }));
    expect(onSubmit).toHaveBeenCalledWith(["X", "Z"], undefined);
  });

  it("seeds the prior selection", () => {
    render(
      <MultiSelectInput
        card={card}
        saving={false}
        existing={priorResponse({ selected: ["Y"] })}
        onSubmit={vi.fn()}
        onSkip={vi.fn()}
      />,
    );
    expect(screen.getByRole("checkbox", { name: "Y" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});

describe("TextInput", () => {
  const card = makeCard({ response_type: "short-text" });

  it("disables submit until non-empty and submits the trimmed value", async () => {
    const onSubmit = vi.fn();
    render(
      <TextInput
        card={card}
        saving={false}
        multiline={false}
        onSubmit={onSubmit}
        onSkip={vi.fn()}
      />,
    );
    const submit = screen.getByRole("button", { name: /submit/i });
    expect(submit).toBeDisabled();
    const user = userEvent.setup();
    await user.type(screen.getByRole("textbox"), "  hello  ");
    expect(submit).toBeEnabled();
    await user.click(submit);
    expect(onSubmit).toHaveBeenCalledWith("hello");
  });
});

describe("DocumentLinkInput", () => {
  const card = makeCard({ response_type: "document-link" });

  it("rejects a non-URL and accepts an http(s) link", async () => {
    const onSubmit = vi.fn();
    render(
      <DocumentLinkInput
        card={card}
        saving={false}
        onSubmit={onSubmit}
        onSkip={vi.fn()}
      />,
    );
    const user = userEvent.setup();
    const input = screen.getByPlaceholderText(/https/i);
    await user.type(input, "not a url");
    await user.tab();
    expect(screen.getByText(/valid http/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /submit/i })).toBeDisabled();

    await user.clear(input);
    await user.type(input, "https://example.com/doc");
    await user.click(screen.getByRole("button", { name: /submit/i }));
    expect(onSubmit).toHaveBeenCalledWith(
      "https://example.com/doc",
      undefined,
    );
  });
});

describe("ContactShareInput", () => {
  const card = makeCard({ response_type: "contact-share" });

  it("requires name + email before submitting", async () => {
    const onSubmit = vi.fn();
    render(
      <ContactShareInput
        card={card}
        saving={false}
        onSubmit={onSubmit}
        onSkip={vi.fn()}
      />,
    );
    const submit = screen.getByRole("button", { name: /share contact/i });
    expect(submit).toBeDisabled();
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText("Name"), "Sam");
    await user.type(screen.getByPlaceholderText("Email"), "sam@x.test");
    await user.type(screen.getByPlaceholderText("Role"), "CTO");
    expect(submit).toBeEnabled();
    await user.click(submit);
    expect(onSubmit).toHaveBeenCalledWith(
      { name: "Sam", email: "sam@x.test", role: "CTO" },
      undefined,
    );
  });
});

describe("ConfirmEditView + EditBody", () => {
  const card = makeCard({
    response_type: "confirm-edit",
    default_value: "Acme, Inc.",
    skip_allowed: true,
  });

  it("confirms and enters edit mode via the two buttons", async () => {
    const onConfirm = vi.fn();
    const onEditStart = vi.fn();
    render(
      <ConfirmEditView
        card={card}
        saving={false}
        onConfirm={onConfirm}
        onEditStart={onEditStart}
        onSkip={vi.fn()}
      />,
    );
    expect(screen.getByText("Acme, Inc.")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /yes, correct/i }));
    expect(onConfirm).toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /needs edit/i }));
    expect(onEditStart).toHaveBeenCalled();
  });

  it("edits: seeds the default, submits the correction, cancels", async () => {
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    render(
      <EditBody
        card={card}
        saving={false}
        onSubmit={onSubmit}
        onCancel={onCancel}
      />,
    );
    const box = screen.getByRole("textbox");
    expect(box).toHaveValue("Acme, Inc.");
    const user = userEvent.setup();
    await user.clear(box);
    await user.type(box, "Acme Corp");
    await user.click(screen.getByRole("button", { name: /save changes/i }));
    expect(onSubmit).toHaveBeenCalledWith("Acme Corp");
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalled();
  });
});
