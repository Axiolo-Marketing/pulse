import { afterEach, describe, expect, it } from "vitest";

import { optInToV2, optOutToV1, prefersV2 } from "./ui-version";

afterEach(() => {
  document.cookie = "pulse_ui=; path=/; max-age=0";
});

describe("ui-version toggle", () => {
  it("defaults to v1 when no cookie is set", () => {
    expect(prefersV2()).toBe(false);
  });

  it("optInToV2 sets the sticky cookie", () => {
    optInToV2();
    expect(document.cookie).toContain("pulse_ui=v2");
    expect(prefersV2()).toBe(true);
  });

  it("optOutToV1 clears the cookie", () => {
    optInToV2();
    optOutToV1();
    expect(prefersV2()).toBe(false);
  });
});
