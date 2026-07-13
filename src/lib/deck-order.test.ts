import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Card, ClientResponse, GenerationRow } from "./api";
import {
  hasTriggerPotential,
  orderDeckCards,
  pollSchedule,
  runPoll,
  spliceIndexFor,
} from "./deck-order";

function card(over: Partial<Card> = {}): Card {
  return {
    id: "c1",
    engagement_id: "e1",
    order_index: 0,
    category: "General",
    title: "Title",
    context: "Context",
    question: "Question?",
    response_type: "confirm-edit",
    options: null,
    default_value: null,
    skip_allowed: true,
    attachment_path: null,
    created_at: "2024-01-01T00:00:00Z",
    ...over,
  };
}

function response(over: Partial<ClientResponse> = {}): ClientResponse {
  return {
    id: "r1",
    card_id: "c1",
    engagement_id: "e1",
    recipient_id: "rec1",
    state: "answered",
    response_value: { confirmed: true },
    viewed_at: "2024-01-01T00:00:00Z",
    answered_at: "2024-01-01T00:00:00Z",
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    ...over,
  };
}

function generation(over: Partial<GenerationRow> = {}): GenerationRow {
  return {
    id: "g1",
    response_id: "r1",
    status: "pending",
    card_ids: [],
    created_at: "2024-01-01T00:00:00Z",
    completed_at: null,
    ...over,
  };
}

describe("orderDeckCards", () => {
  it("sorts shared cards by order_index regardless of input order", () => {
    const cards = [
      card({ id: "b", order_index: 1 }),
      card({ id: "a", order_index: 0 }),
    ];
    expect(orderDeckCards(cards, []).map((c) => c.id)).toEqual(["a", "b"]);
  });

  it("relocates a resolved AI card to directly after its parent", () => {
    const cards = [
      card({ id: "p1", order_index: 0 }),
      card({ id: "p2", order_index: 1 }),
      card({
        id: "ai1",
        order_index: 2,
        source: "ai",
        generated_from_response_id: "resp1",
      }),
    ];
    const responses = [response({ id: "resp1", card_id: "p1" })];
    expect(orderDeckCards(cards, responses).map((c) => c.id)).toEqual([
      "p1",
      "ai1",
      "p2",
    ]);
  });

  it("keeps multiple follow-ups for the same parent in order_index order", () => {
    const cards = [
      card({ id: "p1", order_index: 0 }),
      card({
        id: "ai2",
        order_index: 3,
        source: "ai",
        generated_from_response_id: "resp1",
      }),
      card({
        id: "ai1",
        order_index: 2,
        source: "ai",
        generated_from_response_id: "resp1",
      }),
    ];
    const responses = [response({ id: "resp1", card_id: "p1" })];
    expect(orderDeckCards(cards, responses).map((c) => c.id)).toEqual([
      "p1",
      "ai1",
      "ai2",
    ]);
  });

  it("leaves an unresolvable AI card (no generated_from_response_id) at its order_index position", () => {
    const cards = [
      card({ id: "p1", order_index: 0 }),
      card({ id: "p2", order_index: 1 }),
      card({
        id: "ai1",
        order_index: 2,
        source: "ai",
        generated_from_response_id: null,
      }),
    ];
    expect(orderDeckCards(cards, []).map((c) => c.id)).toEqual([
      "p1",
      "p2",
      "ai1",
    ]);
  });

  it("leaves the AI card in place when the response row can't be found", () => {
    const cards = [
      card({ id: "p1", order_index: 0 }),
      card({
        id: "ai1",
        order_index: 1,
        source: "ai",
        generated_from_response_id: "missing-response",
      }),
    ];
    expect(orderDeckCards(cards, []).map((c) => c.id)).toEqual([
      "p1",
      "ai1",
    ]);
  });

  it("leaves the AI card in place when the parent card isn't in this list", () => {
    const cards = [
      card({ id: "p2", order_index: 0 }),
      card({
        id: "ai1",
        order_index: 1,
        source: "ai",
        generated_from_response_id: "resp1",
      }),
    ];
    const responses = [response({ id: "resp1", card_id: "deleted-parent" })];
    expect(orderDeckCards(cards, responses).map((c) => c.id)).toEqual([
      "p2",
      "ai1",
    ]);
  });

  it("is idempotent — re-ordering an already-ordered list reproduces it", () => {
    const cards = [
      card({ id: "p1", order_index: 0 }),
      card({ id: "p2", order_index: 1 }),
      card({
        id: "ai1",
        order_index: 2,
        source: "ai",
        generated_from_response_id: "resp1",
      }),
    ];
    const responses = [response({ id: "resp1", card_id: "p1" })];
    const once = orderDeckCards(cards, responses);
    const twice = orderDeckCards(once, responses);
    expect(twice.map((c) => c.id)).toEqual(once.map((c) => c.id));
  });
});

describe("spliceIndexFor", () => {
  const cards = [card({ id: "a" }), card({ id: "b" }), card({ id: "c" })];

  it("inserts directly after the parent when the respondent hasn't moved past it", () => {
    expect(spliceIndexFor(cards, "a", 0)).toBe(1);
  });

  it("inserts one past the parent when the parent is ahead of the current index", () => {
    // Parent ("c", index 2) is further along than where the respondent
    // currently sits (index 0) — e.g. the poll resolved unusually fast, or
    // the respondent jumped backward via the card picker. The splice point
    // must track the parent, not the (now stale) current position.
    expect(spliceIndexFor(cards, "c", 0)).toBe(3);
  });

  it("inserts after the current position when the respondent has moved further along (parent behind current)", () => {
    expect(spliceIndexFor(cards, "a", 2)).toBe(3);
  });

  it("falls back to currentIndex + 1 when the parent can't be found", () => {
    expect(spliceIndexFor(cards, "missing", 1)).toBe(2);
  });
});

describe("hasTriggerPotential", () => {
  it("true for a confirm-edit correction — {confirmed:false, correction}", () => {
    expect(
      hasTriggerPotential("confirm-edit", "answered", {
        confirmed: false,
        correction: "use 6 not 5",
      }),
    ).toBe(true);
  });

  it("false when the card was confirmed instead of corrected", () => {
    expect(
      hasTriggerPotential("confirm-edit", "answered", { confirmed: true }),
    ).toBe(false);
  });

  it("false for a blank/whitespace-only correction", () => {
    expect(
      hasTriggerPotential("confirm-edit", "answered", {
        confirmed: false,
        correction: "   ",
      }),
    ).toBe(false);
    expect(
      hasTriggerPotential("confirm-edit", "answered", { confirmed: false }),
    ).toBe(false);
  });

  it("false for any response type other than confirm-edit", () => {
    expect(
      hasTriggerPotential("short-text", "answered", {
        confirmed: false,
        correction: "x",
      }),
    ).toBe(false);
  });

  it("false for a non-answered state (e.g. skipped)", () => {
    expect(hasTriggerPotential("confirm-edit", "skipped", null)).toBe(false);
  });

  it("false for a non-object response_value", () => {
    expect(hasTriggerPotential("confirm-edit", "answered", null)).toBe(false);
    expect(hasTriggerPotential("confirm-edit", "answered", "oops")).toBe(
      false,
    );
  });
});

describe("pollSchedule", () => {
  it("front-loads short delays before settling into a steady cadence", () => {
    expect(pollSchedule).toEqual([
      1500, 2500, 4000, 5000, 5000, 5000, 5000, 5000, 5000,
    ]);
  });
});

describe("runPoll", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("resolves as soon as the generation reaches a terminal status", async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce([generation({ status: "pending" })])
      .mockResolvedValueOnce([
        generation({ status: "completed", card_ids: ["new1"] }),
      ]);
    const controller = new AbortController();
    const promise = runPoll(fetchFn, "r1", controller.signal);

    await vi.advanceTimersByTimeAsync(pollSchedule[0]);
    await vi.advanceTimersByTimeAsync(pollSchedule[1]);

    await expect(promise).resolves.toEqual({
      status: "completed",
      cardIds: ["new1"],
    });
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it("ignores rows for a different response_id", async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce([
        generation({ response_id: "other", status: "completed" }),
      ])
      .mockResolvedValueOnce([generation({ status: "completed" })]);
    const controller = new AbortController();
    const promise = runPoll(fetchFn, "r1", controller.signal);

    await vi.advanceTimersByTimeAsync(pollSchedule[0]);
    await vi.advanceTimersByTimeAsync(pollSchedule[1]);

    await expect(promise).resolves.toEqual({ status: "completed", cardIds: [] });
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it("stops immediately when the signal is already aborted before the first delay elapses", async () => {
    const fetchFn = vi.fn().mockResolvedValue([]);
    const controller = new AbortController();
    const promise = runPoll(fetchFn, "r1", controller.signal);
    controller.abort();

    await vi.advanceTimersByTimeAsync(0);

    await expect(promise).resolves.toBeNull();
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("stops mid-schedule when aborted", async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValue([generation({ status: "pending" })]);
    const controller = new AbortController();
    const promise = runPoll(fetchFn, "r1", controller.signal);

    await vi.advanceTimersByTimeAsync(pollSchedule[0]);
    expect(fetchFn).toHaveBeenCalledTimes(1);
    controller.abort();
    await vi.advanceTimersByTimeAsync(pollSchedule[1]);

    await expect(promise).resolves.toBeNull();
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it("survives a transient fetch error and keeps polling on schedule", async () => {
    const fetchFn = vi
      .fn()
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValueOnce([
        generation({ status: "completed", card_ids: ["new1"] }),
      ]);
    const controller = new AbortController();
    const promise = runPoll(fetchFn, "r1", controller.signal);

    await vi.advanceTimersByTimeAsync(pollSchedule[0]);
    await vi.advanceTimersByTimeAsync(pollSchedule[1]);

    await expect(promise).resolves.toEqual({
      status: "completed",
      cardIds: ["new1"],
    });
  });

  it("gives up and resolves null once the schedule is exhausted", async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValue([generation({ status: "pending" })]);
    const controller = new AbortController();
    const promise = runPoll(fetchFn, "r1", controller.signal);

    for (const ms of pollSchedule) {
      await vi.advanceTimersByTimeAsync(ms);
    }

    await expect(promise).resolves.toBeNull();
    expect(fetchFn).toHaveBeenCalledTimes(pollSchedule.length);
  });
});
