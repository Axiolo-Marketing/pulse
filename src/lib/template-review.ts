export interface ReviewBannerInput {
  template_id: string | null | undefined;
  cardCount: number;
  recipients: { invited_at: string | null }[];
  answeredCount: number;
  dismissed: boolean;
}

/** Show the "review this deck" notice only for a freshly templated deck
 * that nobody has been invited to or answered yet. */
export function shouldShowReviewBanner(i: ReviewBannerInput): boolean {
  return (
    !!i.template_id &&
    i.cardCount > 0 &&
    i.recipients.every((r) => !r.invited_at) &&
    i.answeredCount === 0 &&
    !i.dismissed
  );
}

const key = (engagementId: string): string =>
  `pulse.template-review-dismissed.${engagementId}`;

export function readReviewDismissed(engagementId: string): boolean {
  try {
    return localStorage.getItem(key(engagementId)) === "1";
  } catch {
    return false;
  }
}

export function writeReviewDismissed(engagementId: string): void {
  try {
    localStorage.setItem(key(engagementId), "1");
  } catch {
    // Storage blocked: the notice just comes back next visit.
  }
}
