import { describe, expect, it } from "vitest";

import {
  deckReducer,
  initialDeckState,
  type DeckUiState,
} from "./deck-reducer";

const base = (over: Partial<DeckUiState> = {}): DeckUiState => ({
  index: 1,
  total: 5,
  mode: "view",
  saveError: null,
  modalOpen: false,
  pickerOpen: false,
  showResume: false,
  ...over,
});

describe("initialDeckState", () => {
  it("clamps bootIndex into [0, total]", () => {
    expect(initialDeckState(-3, 5).index).toBe(0);
    expect(initialDeckState(99, 5).index).toBe(5);
    expect(initialDeckState(2, 5).index).toBe(2);
  });

  it("shows the resume banner only when booting mid-deck", () => {
    expect(initialDeckState(0, 5).showResume).toBe(false);
    expect(initialDeckState(2, 5).showResume).toBe(true);
    expect(initialDeckState(5, 5).showResume).toBe(false); // complete
  });
});

describe("navigation resets per-card state", () => {
  it("navigate clears mode/error/modals/resume and clamps", () => {
    const s = base({
      mode: "edit",
      saveError: "x",
      modalOpen: true,
      pickerOpen: true,
      showResume: true,
    });
    const next = deckReducer(s, { type: "navigate", index: 3 });
    expect(next).toMatchObject({
      index: 3,
      mode: "view",
      saveError: null,
      modalOpen: false,
      pickerOpen: false,
      showResume: false,
    });
  });

  it("navigate clamps out-of-range targets", () => {
    expect(deckReducer(base(), { type: "navigate", index: -1 }).index).toBe(0);
    expect(deckReducer(base(), { type: "navigate", index: 9 }).index).toBe(5);
  });

  it("advance moves to the next card (and can reach the complete index)", () => {
    expect(deckReducer(base({ index: 1 }), { type: "advance" }).index).toBe(2);
    expect(deckReducer(base({ index: 4 }), { type: "advance" }).index).toBe(5);
    expect(deckReducer(base({ index: 5 }), { type: "advance" }).index).toBe(5);
  });
});

describe("save + edit + overlay transitions", () => {
  it("saveStart enters saving and clears any prior error", () => {
    const next = deckReducer(base({ saveError: "old" }), { type: "saveStart" });
    expect(next.mode).toBe("saving");
    expect(next.saveError).toBeNull();
  });

  it("saveFailed returns to view and surfaces the message", () => {
    const next = deckReducer(base({ mode: "saving" }), {
      type: "saveFailed",
      message: "nope",
    });
    expect(next.mode).toBe("view");
    expect(next.saveError).toBe("nope");
  });

  it("editStart/editCancel toggle edit mode", () => {
    expect(deckReducer(base(), { type: "editStart" }).mode).toBe("edit");
    expect(
      deckReducer(base({ mode: "edit" }), { type: "editCancel" }).mode,
    ).toBe("view");
  });

  it("modal and picker flags toggle independently", () => {
    expect(deckReducer(base(), { type: "openModal" }).modalOpen).toBe(true);
    expect(
      deckReducer(base({ modalOpen: true }), { type: "closeModal" }).modalOpen,
    ).toBe(false);
    expect(deckReducer(base(), { type: "openPicker" }).pickerOpen).toBe(true);
    expect(
      deckReducer(base({ pickerOpen: true }), { type: "closePicker" })
        .pickerOpen,
    ).toBe(false);
  });
});

describe("cardsInserted (reactive cards live splice)", () => {
  it("grows total and leaves index untouched when mid-deck", () => {
    const s = base({ index: 2, total: 5 });
    const next = deckReducer(s, {
      type: "cardsInserted",
      insertAt: 4,
      count: 1,
    });
    expect(next.total).toBe(6);
    expect(next.index).toBe(2);
  });

  it("grows total by count for a multi-card generation", () => {
    const s = base({ index: 2, total: 5 });
    const next = deckReducer(s, {
      type: "cardsInserted",
      insertAt: 4,
      count: 2,
    });
    expect(next.total).toBe(7);
  });

  it("jumps to insertAt and resets per-card UI state when it lands on the complete screen", () => {
    const s = base({
      index: 5,
      total: 5,
      mode: "edit",
      saveError: "old",
      modalOpen: true,
      pickerOpen: true,
      showResume: true,
    });
    const next = deckReducer(s, {
      type: "cardsInserted",
      insertAt: 5,
      count: 1,
    });
    expect(next).toMatchObject({
      total: 6,
      index: 5,
      mode: "view",
      saveError: null,
      modalOpen: false,
      pickerOpen: false,
      showResume: false,
    });
  });

  it("does not treat an in-deck index equal to a stale total as complete", () => {
    // Sanity check: "was complete" is index === total at the time of the
    // action, not some other coincidental equality.
    const s = base({ index: 3, total: 3 });
    const next = deckReducer(s, {
      type: "cardsInserted",
      insertAt: 3,
      count: 1,
    });
    expect(next.index).toBe(3); // jumped to insertAt (same value here)
    expect(next.total).toBe(4);
  });
});
