import { describe, expect, it } from "vitest";

import { moveSharedCard } from "./card-reorder";

const cards = [
  { id: "a" },
  { id: "ai1", recipient_id: "r1" },
  { id: "b" },
  { id: "c" },
];

describe("moveSharedCard", () => {
  it("swaps a card with its shared neighbour, skipping scoped cards", () => {
    expect(moveSharedCard(cards, "b", "up")).toEqual(["b", "a", "c"]);
    expect(moveSharedCard(cards, "a", "down")).toEqual(["b", "a", "c"]);
  });

  it("returns null at either end", () => {
    expect(moveSharedCard(cards, "a", "up")).toBeNull();
    expect(moveSharedCard(cards, "c", "down")).toBeNull();
  });

  it("returns null for recipient-scoped or unknown cards", () => {
    expect(moveSharedCard(cards, "ai1", "up")).toBeNull();
    expect(moveSharedCard(cards, "zzz", "down")).toBeNull();
  });

  it("does not mutate the input", () => {
    const copy = cards.map((c) => ({ ...c }));
    moveSharedCard(cards, "b", "up");
    expect(cards).toEqual(copy);
  });
});
