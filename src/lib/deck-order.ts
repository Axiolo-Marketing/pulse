// Reactive cards: shared, framework-free deck-ordering + trigger-detection +
// poll helpers. Both the v1 (vanilla TS) and v2 (React) decks import this
// module verbatim — no DOM, no React, pure functions only, so both UIs stay
// byte-for-byte identical in how they order and splice AI-generated cards.
//
// Background: a "Needs edit" correction on a confirm-edit card can trigger a
// backend LLM generation (gated behind `me.reactive_cards_enabled`) that
// inserts follow-up cards scoped to the triggering recipient
// (`cards.recipient_id`, `cards.source === "ai"`,
// `cards.generated_from_response_id` pointing back at the correction's
// response row). This module answers three questions for the deck UI:
//   1. On boot, where should an AI card visually sit relative to its parent?
//      → `orderDeckCards`
//   2. Mid-session, right after a fresh generation lands, where should the
//      new card be spliced into the live deck array?
//      → `spliceIndexFor`
//   3. Right after a save, is this the kind of answer that might have
//      kicked off a generation, so we should start polling?
//      → `hasTriggerPotential`
// `pollSchedule` + `runPoll` are the shared polling loop each deck's runtime
// wires up to `clientApi.generations`.

import type { Card, ClientResponse, GenerationRow, ResponseState, ResponseType } from "./api";

// ── 1. Boot-time ordering ───────────────────────────────────────────────────

/**
 * Order a deck's cards for display: engagement-shared cards sorted by
 * `order_index` (the historical, only ordering), with each AI-generated card
 * (`source === "ai"`) relocated to sit directly after the card whose
 * correction triggered it.
 *
 * A generated card's parent is resolved via
 * `generated_from_response_id -> responses[].id -> that response's card_id`.
 * If the parent can't be resolved (response missing, or the parent card
 * itself isn't in this list), the AI card is left exactly where a plain
 * `order_index` sort would have put it — generated cards always get
 * `order_index = max + 1` at creation, so that's the end of the deck,
 * same as before this feature existed.
 *
 * Multiple AI cards sharing one parent keep their relative `order_index`
 * order (the order they were generated in) directly after that parent.
 *
 * Pure and idempotent: ordering only ever reads each card's own
 * `order_index` (never its position in the input array), so calling this
 * again on an already-ordered list reproduces the same result.
 */
export function orderDeckCards(
  cards: Card[],
  responses: ClientResponse[],
): Card[] {
  const sorted = [...cards].sort((a, b) => a.order_index - b.order_index);
  const cardIds = new Set(sorted.map((c) => c.id));
  const responseById = new Map(responses.map((r) => [r.id, r]));

  // Children queued to be spliced right after their parent, keyed by parent
  // card id. Built in `sorted` (order_index) order, so multiple children of
  // one parent keep their relative order.
  const childrenByParent = new Map<string, Card[]>();
  const relocated = new Set<string>();

  for (const c of sorted) {
    if (c.source !== "ai") continue;
    const parentId = resolveParentCardId(c, responseById);
    if (!parentId || parentId === c.id || !cardIds.has(parentId)) continue;
    const list = childrenByParent.get(parentId);
    if (list) list.push(c);
    else childrenByParent.set(parentId, [c]);
    relocated.add(c.id);
  }

  const result: Card[] = [];
  for (const c of sorted) {
    if (relocated.has(c.id)) continue; // placed next to its parent instead
    result.push(c);
    const children = childrenByParent.get(c.id);
    if (children) result.push(...children);
  }
  return result;
}

function resolveParentCardId(
  aiCard: Card,
  responseById: Map<string, ClientResponse>,
): string | null {
  if (!aiCard.generated_from_response_id) return null;
  const response = responseById.get(aiCard.generated_from_response_id);
  return response?.card_id ?? null;
}

// ── 2. Live-session splice point ────────────────────────────────────────────

/**
 * Where to splice a freshly-generated card into the *live* deck array.
 *
 * Deliberately never inserts at or before wherever the respondent currently
 * is — by the time a generation resolves (up to ~40s of polling) they may
 * already have moved past the parent card, and yanking a card in behind
 * their current position would shift what's on screen out from under them.
 * So the splice point is always one past whichever is further along: the
 * parent's position, or the respondent's current position.
 *
 * (On the *next* boot, `orderDeckCards` re-derives the "right after parent"
 * placement from `generated_from_response_id` regardless of where this live
 * splice landed, so the two never need to agree — this one only has to be
 * safe for the current session.)
 */
export function spliceIndexFor(
  cards: Card[],
  parentCardId: string,
  currentIndex: number,
): number {
  const parentIndex = cards.findIndex((c) => c.id === parentCardId);
  return Math.max(parentIndex, currentIndex) + 1;
}

// ── 3. Trigger detection ────────────────────────────────────────────────────

/**
 * Mirrors the backend's `extract_trigger_text` gate: only a *saved*
 * confirm-edit correction can have kicked off a generation. Both decks
 * produce the exact same `response_value` shape for this action —
 * `{ confirmed: false, correction: "<text>" }` — see `performSave` in
 * `src/scripts/app.ts` and `encodeResponse` in
 * `src/components/deck/encode-response.ts`.
 *
 * This is a client-side *hint* only ("should we bother polling?") — the
 * backend re-derives and re-validates the trigger independently, so a false
 * positive here just means one wasted poll loop that resolves to nothing.
 */
export function hasTriggerPotential(
  responseType: ResponseType,
  state: ResponseState,
  responseValue: unknown,
): boolean {
  if (responseType !== "confirm-edit") return false;
  if (state !== "answered") return false;
  if (typeof responseValue !== "object" || responseValue === null) return false;
  const v = responseValue as { confirmed?: unknown; correction?: unknown };
  if (v.confirmed !== false) return false;
  return typeof v.correction === "string" && v.correction.trim().length > 0;
}

// ── 4. Shared poll loop ─────────────────────────────────────────────────────

/** Delays (ms) between poll attempts after a qualifying save — front-loaded
 * (the common case resolves in a couple seconds) tapering to a steady 5s
 * cadence, for a ~40s total budget before giving up silently. */
export const pollSchedule: readonly number[] = [
  1500, 2500, 4000, 5000, 5000, 5000, 5000, 5000, 5000,
];

export interface PollOutcome {
  status: "completed" | "skipped" | "failed";
  cardIds: string[];
}

/** Resolves after `ms`, or immediately if `signal` aborts first. */
function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = (): void => {
      clearTimeout(timer);
      resolve();
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * Poll `fetchGenerations` on `pollSchedule` until the generation for
 * `responseId` reaches a terminal status, the caller aborts via `signal`, or
 * the schedule runs out (in which case this resolves `null` — the deck just
 * gives up quietly, per product decision: a failed/slow generation is
 * invisible to the respondent).
 *
 * A transient fetch error doesn't abort the loop — it's treated like a
 * not-yet-terminal tick and retried on the next scheduled delay.
 */
export async function runPoll(
  fetchGenerations: () => Promise<GenerationRow[]>,
  responseId: string,
  signal: AbortSignal,
): Promise<PollOutcome | null> {
  for (const ms of pollSchedule) {
    await delay(ms, signal);
    if (signal.aborted) return null;

    let rows: GenerationRow[];
    try {
      rows = await fetchGenerations();
    } catch {
      continue;
    }
    if (signal.aborted) return null;

    const row = rows.find((r) => r.response_id === responseId);
    if (row && row.status !== "pending") {
      return { status: row.status, cardIds: row.card_ids };
    }
  }
  return null;
}
