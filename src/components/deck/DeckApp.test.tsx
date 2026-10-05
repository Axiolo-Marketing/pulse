import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, clientApi } from "@/lib/api";
import DeckApp from "./DeckApp";

vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return { ...actual, clientApi: { me: vi.fn(), cards: vi.fn(), responses: vi.fn(), uploads: vi.fn(), logoObjectUrl: vi.fn() } };
});

describe("DeckApp boot errors", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/?t=abc");
    vi.mocked(clientApi.me).mockReset();
    vi.mocked(clientApi.cards).mockReset();
  });

  it("shows not-found when /me is rejected with an ApiError", async () => {
    vi.mocked(clientApi.me).mockRejectedValue(new ApiError(404, "nope"));
    render(<DeckApp />);
    expect(await screen.findByText("We could not find your engagement")).toBeInTheDocument();
  });

  it("shows the generic error on a network failure", async () => {
    vi.mocked(clientApi.me).mockRejectedValue(new TypeError("Failed to fetch"));
    render(<DeckApp />);
    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
  });

  it("shows the generic error when cards fail to load", async () => {
    vi.mocked(clientApi.me).mockResolvedValue({ org_branding: null } as never);
    vi.mocked(clientApi.cards).mockRejectedValue(new ApiError(500, "boom"));
    vi.mocked(clientApi.responses).mockResolvedValue([]);
    vi.mocked(clientApi.uploads).mockResolvedValue([]);
    render(<DeckApp />);
    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
  });
});
