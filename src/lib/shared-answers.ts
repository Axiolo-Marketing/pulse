import type {
  AnswerEvent,
  Card,
  ClientResponse,
  EngagementDetail,
  Recipient,
} from "./api";

/** One line per thing a logged change did, for the history list. */
export function describeChange(card: Card, ev: AnswerEvent): string[] {
  const change = ev.change ?? {};
  if (change.cells) {
    const cfg = card.block_config ?? { rows: [], columns: [] };
    const rowLabel = (k: string): string => cfg.rows.find((r) => r.key === k)?.label ?? k;
    const colLabel = (k: string): string => cfg.columns.find((c) => c.key === k)?.label ?? k;
    const lines: string[] = [];
    for (const [rk, row] of Object.entries(change.cells)) {
      for (const [ck, v] of Object.entries(row)) {
        lines.push(`${rowLabel(rk)} · ${colLabel(ck)}: ${v ? v : "cleared"}`);
      }
    }
    return lines;
  }
  const text = (change.text ?? "").trim();
  if (!text) return ["Cleared the answer"];
  return [`“${text.length > 160 ? `${text.slice(0, 159)}…` : text}”`];
}

const SHARED_ID = "shared";

/** For the Markdown export: shared answers as a single pseudo-respondent's
 * responses, so the per-recipient export renders one answer per card. */
export function sharedExportDetail(detail: EngagementDetail): EngagementDetail {
  const shared = detail.shared_answers ?? [];
  const recipient: Recipient = {
    id: SHARED_ID,
    engagement_id: detail.engagement.id,
    email: null,
    name: "Shared answers",
    token: "",
    last_active_at: null,
    invited_at: null,
    last_reminded_at: null,
    reminder_count: 0,
    unsubscribed_at: null,
    created_at: detail.engagement.created_at,
    completed_count: shared.length,
    total_cards: detail.cards.length,
  };
  const responses: ClientResponse[] = shared.map((s) => ({
    id: `shared-${s.card_id}`,
    card_id: s.card_id,
    engagement_id: detail.engagement.id,
    recipient_id: SHARED_ID,
    state: "answered",
    response_value: s.value,
    viewed_at: null,
    answered_at: s.updated_at,
    created_at: s.updated_at,
    updated_at: s.updated_at,
  }));
  return { ...detail, recipients: [recipient], responses, uploads: [] };
}
