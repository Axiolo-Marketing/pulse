import { describe, expect, it } from "vitest";

import { parseRespondents } from "./RespondentPicker";

describe("parseRespondents", () => {
  it("parses bare emails", () => {
    expect(parseRespondents("a@x.com")).toEqual([{ email: "a@x.com", name: null }]);
  });

  it("parses Name <email>, with or without quotes", () => {
    expect(parseRespondents("Ann Lee <ann@x.com>")).toEqual([
      { email: "ann@x.com", name: "Ann Lee" },
    ]);
    expect(parseRespondents('"Bob" <bob@y.com>')).toEqual([
      { email: "bob@y.com", name: "Bob" },
    ]);
  });

  it("parses a mixed list separated by commas, semicolons or newlines", () => {
    expect(
      parseRespondents("a@x.com, Bob <b@y.com>; c@z.com\nDee <d@w.com>"),
    ).toEqual([
      { email: "a@x.com", name: null },
      { email: "b@y.com", name: "Bob" },
      { email: "c@z.com", name: null },
      { email: "d@w.com", name: "Dee" },
    ]);
  });

  it("drops junk", () => {
    expect(parseRespondents("not an email")).toEqual([]);
    expect(parseRespondents("")).toEqual([]);
  });
});
