import type { EngagementDetail } from "@/lib/api";

export interface CardDeletionImpact {
  questions: number;
  /** Answered / skipped / flagged responses (a mere "viewed" doesn't count). */
  answers: number;
  /** Respondents who lose at least one of those answers. */
  respondents: number;
  uploads: number;
}

/** What deleting these cards (or every card) would erase. */
export function cardDeletionImpact(
  detail: Pick<EngagementDetail, "cards" | "responses" | "uploads" | "shared_answers">,
  cardIds: string[] | "all",
): CardDeletionImpact {
  const ids = new Set(cardIds === "all" ? detail.cards.map((c) => c.id) : cardIds);
  const answers = detail.responses.filter(
    (r) => ids.has(r.card_id) && r.state !== "viewed",
  );
  // Shared-answers mode keeps one answer per card outside `responses`.
  const shared = (detail.shared_answers ?? []).filter((a) => ids.has(a.card_id)).length;
  return {
    questions: detail.cards.filter((c) => ids.has(c.id)).length,
    answers: answers.length + shared,
    respondents: new Set(answers.map((r) => r.recipient_id)).size,
    uploads: detail.uploads.filter((u) => ids.has(u.card_id)).length,
  };
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** Confirm-dialog copy for a bulk delete. */
export function cardDeletionDescription(impact: CardDeletionImpact): string {
  const lost =
    impact.answers > 0
      ? `This removes ${plural(impact.questions, "question")} and ${plural(impact.answers, "answer")} from ${plural(impact.respondents, "respondent")}${impact.uploads > 0 ? `, including ${plural(impact.uploads, "uploaded file")}` : ""}. This can't be undone.`
      : `This removes ${plural(impact.questions, "question")}. No one has answered ${impact.questions === 1 ? "it" : "them"} yet.`;
  return `${lost} Respondent links stay the same, so you can add or import new questions for the same people.`;
}
