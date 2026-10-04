import { afterEach, describe, expect, it, vi } from "vitest";

import {
  honorReturnTo,
  readReturnTo,
  sanitizeReturnTo,
  stashReturnTo,
  takeStashedReturnTo,
} from "./return-to";

const O = "https://pulse.example.com";

describe("sanitizeReturnTo", () => {
  it.each([
    ["/authorize/consent?x=1&y=2", "/authorize/consent?x=1&y=2"],
    ["/admin/#settings", "/admin/#settings"],
    ["/", "/"],
  ])("accepts %s", (raw, out) => {
    expect(sanitizeReturnTo(raw, O)).toBe(out);
  });

  it.each([
    "//evil.com",
    "//evil.com/path",
    "https://evil.com",
    "http://pulse.example.com/x",
    "/\\evil.com",
    "\\\\evil.com",
    "javascript:alert(1)",
    "data:text/html,x",
    "/\t/evil.com",
    "/\n/evil.com",
    "evil.com",
    "",
    null,
    undefined,
  ])("rejects %s", (raw) => {
    expect(sanitizeReturnTo(raw as string | null | undefined, O)).toBeNull();
  });
});

describe("stash + honor", () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("reads return_to from a query string", () => {
    expect(readReturnTo("?return_to=%2Fauthorize%2Fconsent%3Fa%3D1")).toBe(
      "/authorize/consent?a=1",
    );
    expect(readReturnTo("?return_to=%2F%2Fevil.com")).toBeNull();
  });

  it("round-trips a stashed target once", () => {
    stashReturnTo("/authorize/consent?a=1");
    expect(takeStashedReturnTo()).toBe("/authorize/consent?a=1");
    expect(takeStashedReturnTo()).toBeNull();
  });

  it("expires stale stashes and rejects unsafe ones", () => {
    vi.useFakeTimers();
    stashReturnTo("/authorize/consent");
    vi.advanceTimersByTime(11 * 60 * 1000);
    expect(takeStashedReturnTo()).toBeNull();
    sessionStorage.setItem(
      "pulse:return_to",
      JSON.stringify({ target: "//evil.com", at: Date.now() }),
    );
    expect(takeStashedReturnTo()).toBeNull();
  });

  it("honorReturnTo navigates only for safe targets", () => {
    const replace = vi.fn();
    vi.stubGlobal("location", {
      origin: window.location.origin,
      search: "?return_to=%2Fauthorize%2Fconsent",
      replace,
    });
    expect(honorReturnTo()).toBe(true);
    expect(replace).toHaveBeenCalledWith("/authorize/consent");
    vi.stubGlobal("location", {
      origin: window.location.origin,
      search: "?return_to=https%3A%2F%2Fevil.com",
      replace,
    });
    replace.mockClear();
    expect(honorReturnTo()).toBe(false);
    expect(replace).not.toHaveBeenCalled();
  });
});
