/** The minimal card shape the reorder helpers need. */
export interface ReorderableCard {
  id: string;
  recipient_id?: string | null;
}

/** Cards every respondent sees (not a recipient's own AI follow-up). */
export function isSharedCard(card: ReorderableCard): boolean {
  return !card.recipient_id;
}

/**
 * New order of the shared card ids after moving `cardId` one step.
 * Recipient-scoped cards are ignored. Returns `null` when the card is not
 * a shared card or is already at that end of the list.
 *
 * `cards` must already be in display order.
 */
export function moveSharedCard(
  cards: ReorderableCard[],
  cardId: string,
  direction: "up" | "down",
): string[] | null {
  const ids = cards.filter(isSharedCard).map((c) => c.id);
  const from = ids.indexOf(cardId);
  if (from === -1) return null;
  const to = direction === "up" ? from - 1 : from + 1;
  if (to < 0 || to >= ids.length) return null;
  const next = [...ids];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}
