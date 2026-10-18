import { describe, expect, it } from "vitest";

import type { Card, ClientResponse, UploadRow } from "@/lib/api";

import { cardDeletionDescription, cardDeletionImpact } from "./card-deletion";

const cards = [{ id: "a" }, { id: "b" }, { id: "c" }] as Card[];
const responses = [
  { card_id: "a", recipient_id: "r1", state: "answered" },
  { card_id: "a", recipient_id: "r2", state: "skipped" },
  { card_id: "b", recipient_id: "r1", state: "answered" },
  { card_id: "c", recipient_id: "r3", state: "viewed" },
] as ClientResponse[];
const uploads = [{ card_id: "b" }] as UploadRow[];
const detail = { cards, responses, uploads };

describe("cardDeletionImpact", () => {
  it("counts answers, respondents and files for the selected cards", () => {
    expect(cardDeletionImpact(detail, ["a"])).toEqual({
      questions: 1,
      answers: 2,
      respondents: 2,
      uploads: 0,
    });
  });

  it("covers every card with 'all', ignoring mere views", () => {
    expect(cardDeletionImpact(detail, "all")).toEqual({
      questions: 3,
      answers: 3,
      respondents: 2,
      uploads: 1,
    });
  });
});

describe("cardDeletionDescription", () => {
  it("spells out lost answers and files", () => {
    expect(cardDeletionDescription(cardDeletionImpact(detail, "all"))).toBe(
      "This removes 3 questions and 3 answers from 2 respondents, including 1 uploaded file. " +
        "This can't be undone. Respondent links stay the same, so you can add or import new " +
        "questions for the same people.",
    );
  });

  it("says when nothing has been answered", () => {
    expect(cardDeletionDescription(cardDeletionImpact(detail, ["c"]))).toMatch(
      /^This removes 1 question\. No one has answered it yet\./,
    );
  });
});
