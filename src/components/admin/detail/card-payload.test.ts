import { describe, expect, it } from "vitest";

import type { Card } from "@/lib/api";
import {
  buildCardFields,
  optionsError,
  parseOptions,
  type CardFormValues,
} from "./card-payload";

const form: CardFormValues = {
  category: " Brand ",
  title: "T",
  context: "C",
  question: "Q",
  responseType: "confirm-edit",
  optionsText: "",
  defaultValue: "  Acme  ",
  skipAllowed: true,
  attachmentPath: "",
};

describe("optionsError", () => {
  it("requires an option for single/multi select only", () => {
    expect(optionsError("single-select", " \n ")).toMatch(/at least one/i);
    expect(optionsError("multi-select", "")).toMatch(/at least one/i);
    expect(optionsError("single-select", "A")).toBeNull();
    expect(optionsError("short-text", "")).toBeNull();
  });

  it("parses one option per non-blank line", () => {
    expect(parseOptions(" A \n\n B ")).toEqual(["A", "B"]);
  });
});

describe("buildCardFields", () => {
  it("sends the default value on create for confirm-edit", () => {
    expect(buildCardFields(form).default_value).toBe("Acme");
  });

  it("omits default_value and options for other types on create", () => {
    const f = buildCardFields({ ...form, responseType: "short-text" });
    expect("default_value" in f).toBe(false);
    expect("options" in f).toBe(false);
  });

  it("leaves a legacy card's hidden default_value untouched on edit", () => {
    const legacy = { default_value: "old", response_type: "short-text" } as Card;
    const f = buildCardFields({ ...form, responseType: "short-text", defaultValue: "old" }, legacy);
    expect("default_value" in f).toBe(false);
  });

  it("omits an unchanged default_value and sends a changed or cleared one", () => {
    const existing = { default_value: "Acme" } as Card;
    expect("default_value" in buildCardFields(form, existing)).toBe(false);
    expect(buildCardFields({ ...form, defaultValue: "New" }, existing).default_value).toBe("New");
    expect(buildCardFields({ ...form, defaultValue: " " }, existing).default_value).toBeNull();
  });

  it("sends parsed options for select types", () => {
    const f = buildCardFields({ ...form, responseType: "single-select", optionsText: "A\nB" });
    expect(f.options).toEqual(["A", "B"]);
  });
});
