import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import UnsubscribeApp from "./UnsubscribeApp";

// Component test: validates the RTL harness and the three terminal states.
// fetch is the single seam — UnsubscribeApp posts to the API and renders by result.

afterEach(() => {
  vi.restoreAllMocks();
  window.history.pushState({}, "", "/");
});

describe("UnsubscribeApp", () => {
  it("shows the success state when the API accepts the token", async () => {
    window.history.pushState({}, "", "/v2/unsubscribe?u=goodtoken");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true } as Response),
    );

    render(<UnsubscribeApp />);

    expect(
      await screen.findByText("You're unsubscribed"),
    ).toBeInTheDocument();
  });

  it("shows the invalid state when the link has no token", async () => {
    window.history.pushState({}, "", "/v2/unsubscribe");

    render(<UnsubscribeApp />);

    expect(await screen.findByText("Invalid link")).toBeInTheDocument();
  });

  it("shows the expired state on a non-ok response", async () => {
    window.history.pushState({}, "", "/v2/unsubscribe?u=bad");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false } as Response),
    );

    render(<UnsubscribeApp />);

    expect(await screen.findByText("Link expired")).toBeInTheDocument();
  });
});
