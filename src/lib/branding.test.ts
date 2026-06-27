import { afterEach, describe, expect, it } from "vitest";

import {
  applyBranding,
  BRANDING_DEFAULTS,
  darken,
  mixWithWhite,
} from "./branding";

// Characterization tests: pin the existing branding behaviour so the v2 theme
// (which references the same :root custom properties) keeps re-theming live.

describe("colour math", () => {
  it("darken moves a colour toward black", () => {
    expect(darken("#FFFFFF", 0)).toBe("#FFFFFF");
    expect(darken("#FFFFFF", 1)).toBe("#000000");
    // Halfway darken of pure white is mid-grey.
    expect(darken("#FFFFFF", 0.5)).toBe("#808080");
  });

  it("mixWithWhite moves a colour toward white", () => {
    expect(mixWithWhite("#000000", 0)).toBe("#000000");
    expect(mixWithWhite("#000000", 1)).toBe("#FFFFFF");
    expect(mixWithWhite("#000000", 0.5)).toBe("#808080");
  });

  it("returns the input unchanged for non-hex values", () => {
    expect(darken("not-a-colour", 0.5)).toBe("not-a-colour");
    expect(mixWithWhite("rgb(0,0,0)", 0.5)).toBe("rgb(0,0,0)");
  });
});

describe("applyBranding live theming", () => {
  afterEach(() => {
    document.documentElement.removeAttribute("style");
  });

  const read = (name: string): string =>
    document.documentElement.style.getPropertyValue(name).trim();

  it("falls back to Axiolo defaults when branding is null", () => {
    applyBranding(null);
    expect(read("--primary")).toBe(BRANDING_DEFAULTS.brand_color);
    expect(read("--primary-dark")).toBe(BRANDING_DEFAULTS.brand_dark);
    expect(read("--primary-soft")).toBe(BRANDING_DEFAULTS.brand_soft);
    expect(read("--surface-muted")).toBe(BRANDING_DEFAULTS.background_color);
    expect(read("--ink")).toBe(BRANDING_DEFAULTS.text_color);
  });

  it("writes a custom brand colour and derives its shades onto :root", () => {
    applyBranding({ brand_color: "#FF0000" } as Parameters<
      typeof applyBranding
    >[0]);
    expect(read("--primary")).toBe("#FF0000");
    // Derived shades are valid 6-digit hex, not the raw input.
    expect(read("--primary-dark")).toMatch(/^#[0-9A-F]{6}$/);
    expect(read("--primary-soft")).toMatch(/^#[0-9A-F]{6}$/);
    expect(read("--primary-soft")).not.toBe("#FF0000");
  });
});
