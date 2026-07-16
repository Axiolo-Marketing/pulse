import { describe, expect, it } from "vitest";

import { encodeResponse } from "./encode-response";

describe("encodeResponse — response_value parity with app.ts", () => {
  it("confirm → answered { confirmed: true }", () => {
    expect(encodeResponse({ kind: "confirm" })).toEqual({
      state: "answered",
      response_value: { confirmed: true },
    });
  });

  it("edit → answered { confirmed: false, correction }", () => {
    expect(
      encodeResponse({ kind: "edit", correction: "use 6 not 5" }),
    ).toEqual({
      state: "answered",
      response_value: { confirmed: false, correction: "use 6 not 5" },
    });
  });

  it("single-select → answered { selected }", () => {
    expect(encodeResponse({ kind: "single-select", option: "B" })).toEqual({
      state: "answered",
      response_value: { selected: "B" },
    });
  });

  it("note (Send note, nothing selected) → answered { note }", () => {
    expect(encodeResponse({ kind: "note", note: "call me first" })).toEqual({
      state: "answered",
      response_value: { note: "call me first" },
    });
  });

  it("note with a highlighted option keeps it → { selected, note }", () => {
    expect(
      encodeResponse({ kind: "note", note: "but rename it", option: "B" }),
    ).toEqual({
      state: "answered",
      response_value: { selected: "B", note: "but rename it" },
    });
  });

  it("multi-select → answered { selected: [...] }", () => {
    expect(
      encodeResponse({ kind: "multi-select", options: ["A", "C"] }),
    ).toEqual({
      state: "answered",
      response_value: { selected: ["A", "C"] },
    });
  });

  it("text / link → answered { text } / { url }", () => {
    expect(encodeResponse({ kind: "text", text: "hi" }).response_value).toEqual(
      { text: "hi" },
    );
    expect(
      encodeResponse({ kind: "link", url: "https://x.test" }).response_value,
    ).toEqual({ url: "https://x.test" });
  });

  it("contact → answered { name, email, role }", () => {
    expect(
      encodeResponse({
        kind: "contact",
        name: "Sam",
        email: "s@x.test",
        role: "CTO",
      }).response_value,
    ).toEqual({ name: "Sam", email: "s@x.test", role: "CTO" });
  });
});

describe("withNote folding", () => {
  it("folds a note into an object value", () => {
    expect(
      encodeResponse({ kind: "single-select", option: "B", note: "n" })
        .response_value,
    ).toEqual({ selected: "B", note: "n" });
  });

  it("a skip with a note becomes { note }; without a note stays null", () => {
    expect(encodeResponse({ kind: "skip" })).toEqual({
      state: "skipped",
      response_value: null,
    });
    expect(
      encodeResponse({ kind: "skip", note: "later" }).response_value,
    ).toEqual({ note: "later" });
  });
});

describe("files-continue", () => {
  it("with files → answered { file_ids }", () => {
    expect(
      encodeResponse({ kind: "files-continue" }, ["u1", "u2"]),
    ).toEqual({
      state: "answered",
      response_value: { file_ids: ["u1", "u2"] },
    });
  });

  it("no files, no note → skipped null", () => {
    expect(encodeResponse({ kind: "files-continue" }, [])).toEqual({
      state: "skipped",
      response_value: null,
    });
  });

  it("no files but a note → answered { note }", () => {
    expect(
      encodeResponse({ kind: "files-continue", note: "see email" }, []),
    ).toEqual({ state: "answered", response_value: { note: "see email" } });
  });
});

describe("voice supplement", () => {
  it("promotes a would-be skip to answered when a voice note exists", () => {
    expect(encodeResponse({ kind: "skip" }, [], true).state).toBe("answered");
  });

  it("leaves an already-answered state untouched", () => {
    const r = encodeResponse({ kind: "text", text: "hi" }, [], true);
    expect(r.state).toBe("answered");
    expect(r.response_value).toEqual({ text: "hi" });
  });
});
