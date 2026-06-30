import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, invitesApi } from "@/lib/api";
import InviteApp from "./InviteApp";

// Mock only invitesApi; keep the real ApiError class so `instanceof` checks work.
vi.mock("@/lib/api", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/api")>();
  return {
    ...actual,
    invitesApi: {
      resolve: vi.fn(),
      acceptWithPassword: vi.fn(),
      acceptWithOAuth: vi.fn(),
    },
  };
});

const mockInvites = vi.mocked(invitesApi);

const pendingMeta = {
  org_name: "Acme Co",
  email: "sam@acme.test",
  role: "member",
  expires_at: "2099-01-01T00:00:00Z",
  status: "pending" as const,
};

// jsdom's `window.location.assign` is non-configurable, so replace the whole
// `location` with a plain object: a controllable `search` plus an `assign` spy
// we can assert navigation targets against.
const assignMock = vi.fn();

function setToken(token?: string): void {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: {
      search: token ? `?token=${token}` : "",
      href: "http://localhost/",
      origin: "http://localhost",
      pathname: "/v2/invite",
      assign: assignMock,
    } as unknown as Location,
  });
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("InviteApp", () => {
  it("shows 'No invite token' and never calls the API without a token", async () => {
    setToken();
    render(<InviteApp />);
    expect(await screen.findByText("No invite token")).toBeInTheDocument();
    expect(mockInvites.resolve).not.toHaveBeenCalled();
  });

  it("renders the pending invite with OAuth buttons + password form", async () => {
    setToken("tok");
    mockInvites.resolve.mockResolvedValue(pendingMeta);
    render(<InviteApp />);

    expect(await screen.findByText("Join Acme Co")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /continue with google/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /continue with microsoft/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it.each([
    ["expired", "This invite has expired"],
    ["accepted", "Invite already used"],
    ["revoked", "This invite was revoked"],
  ] as const)("renders the %s terminal state", async (status, title) => {
    setToken("tok");
    mockInvites.resolve.mockResolvedValue({ ...pendingMeta, status });
    render(<InviteApp />);
    expect(await screen.findByText(title)).toBeInTheDocument();
  });

  it("shows 'Invite not found' on a 404", async () => {
    setToken("tok");
    mockInvites.resolve.mockRejectedValue(new ApiError(404, "nope"));
    render(<InviteApp />);
    expect(await screen.findByText("Invite not found")).toBeInTheDocument();
  });

  it("shows 'Could not load invite' with the detail on a non-404 resolve error", async () => {
    setToken("tok");
    mockInvites.resolve.mockRejectedValue(
      new ApiError(500, "Internal server error"),
    );
    render(<InviteApp />);
    expect(
      await screen.findByText("Could not load invite"),
    ).toBeInTheDocument();
    expect(screen.getByText(/internal server error/i)).toBeInTheDocument();
  });

  it("validates password length before calling the API", async () => {
    setToken("tok");
    mockInvites.resolve.mockResolvedValue(pendingMeta);
    render(<InviteApp />);
    await screen.findByText("Join Acme Co");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/password/i), "short");
    await user.click(
      screen.getByRole("button", { name: /accept and sign in/i }),
    );

    expect(await screen.findByText(/at least 8 characters/i)).toBeInTheDocument();
    expect(mockInvites.acceptWithPassword).not.toHaveBeenCalled();
  });

  it("accepts with a valid password (name trimmed, optional)", async () => {
    setToken("tok");
    mockInvites.resolve.mockResolvedValue(pendingMeta);
    mockInvites.acceptWithPassword.mockResolvedValue({
      user_id: "u",
      org_id: "o",
      role: "member",
    });
    render(<InviteApp />);
    await screen.findByText("Join Acme Co");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/your name/i), "  Sam  ");
    await user.type(screen.getByLabelText(/password/i), "supersecret");
    await user.click(
      screen.getByRole("button", { name: /accept and sign in/i }),
    );

    await waitFor(() =>
      expect(mockInvites.acceptWithPassword).toHaveBeenCalledWith("tok", {
        password: "supersecret",
        name: "Sam",
      }),
    );
    await waitFor(() => expect(assignMock).toHaveBeenCalledWith("/admin/"));
  });

  it("surfaces the 'account exists' message on a 409", async () => {
    setToken("tok");
    mockInvites.resolve.mockResolvedValue(pendingMeta);
    mockInvites.acceptWithPassword.mockRejectedValue(
      new ApiError(409, "exists"),
    );
    render(<InviteApp />);
    await screen.findByText("Join Acme Co");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/password/i), "supersecret");
    await user.click(
      screen.getByRole("button", { name: /accept and sign in/i }),
    );

    expect(
      await screen.findByText(/account already exists/i),
    ).toBeInTheDocument();
  });

  it("flips to the accepted terminal state on a concurrent-accept 410", async () => {
    setToken("tok");
    mockInvites.resolve.mockResolvedValue(pendingMeta);
    mockInvites.acceptWithPassword.mockRejectedValue(
      new ApiError(410, "gone"),
    );
    render(<InviteApp />);
    await screen.findByText("Join Acme Co");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/password/i), "supersecret");
    await user.click(
      screen.getByRole("button", { name: /accept and sign in/i }),
    );

    expect(await screen.findByText("Invite already used")).toBeInTheDocument();
  });

  it("starts the OAuth flow on provider click", async () => {
    setToken("tok");
    mockInvites.resolve.mockResolvedValue(pendingMeta);
    mockInvites.acceptWithOAuth.mockResolvedValue({
      redirect_url: "https://oauth.test/go",
    });
    render(<InviteApp />);
    await screen.findByText("Join Acme Co");

    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: /continue with google/i }),
    );

    await waitFor(() =>
      expect(mockInvites.acceptWithOAuth).toHaveBeenCalledWith("tok", "google"),
    );
    await waitFor(() =>
      expect(assignMock).toHaveBeenCalledWith("https://oauth.test/go"),
    );
  });

  it("shows an error and re-enables the OAuth buttons on provider failure", async () => {
    setToken("tok");
    mockInvites.resolve.mockResolvedValue(pendingMeta);
    mockInvites.acceptWithOAuth.mockRejectedValue(
      new ApiError(400, "oauth failed"),
    );
    render(<InviteApp />);
    await screen.findByText("Join Acme Co");

    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: /continue with google/i }),
    );

    expect(await screen.findByText(/oauth failed/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /continue with google/i }),
    ).not.toBeDisabled();
    expect(assignMock).not.toHaveBeenCalled();
  });
});
