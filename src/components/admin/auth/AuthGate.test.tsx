import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "./AuthGate";

function renderGate(): void {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthGate />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("AuthGate OAuth error codes", () => {
  it.each([
    ["invitation_required", /need an invitation/i],
    ["invite_invalid", /invalid or has expired/i],
    ["email_unverified", /verify your email/i],
  ])("shows a message for %s and strips the param", async (code, re) => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false })));
    window.history.replaceState({}, "", `/?error=${code}`);
    renderGate();
    expect(await screen.findByRole("alert")).toHaveTextContent(re);
    expect(window.location.search).toBe("");
  });

  it("hides provider buttons the backend has not configured", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ google: true, microsoft: false }),
      })),
    );
    renderGate();
    expect(
      await screen.findByRole("button", { name: /continue with google/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /continue with microsoft/i }),
    ).not.toBeInTheDocument();
  });
});
