// Derived engagement status — single source of truth for the admin list.
//
// There is NO status column. Status is computed purely from the recipient
// rollup counts the API already returns (`recipients_count`,
// `completed_recipients`, `answered_responses`). Post multi-respondent migration, progress is
// measured across recipients (each has its own magic link and answers the
// shared cards independently), not across cards. Keep this module pure +
// dependency-free so it stays unit-testable and reusable across the status
// pill, the per-client rollup, and the status filter/sort.

export type EngagementStatus = "waiting" | "in_progress" | "complete";

/** Minimal shape needed to derive status — a structural subset of
 * `EngagementSummary` so any object carrying the two recipient counts
 * works. */
export interface StatusCounts {
  recipients_count: number;
  completed_recipients: number;
  /** Answered + skipped responses across every respondent. */
  answered_responses?: number;
}

/**
 * Derive an engagement's status from its recipient rollup:
 *  - `complete`     — has recipients AND every recipient has completed every
 *                     card (`recipients_count > 0 && completed_recipients
 *                     === recipients_count`)
 *  - `in_progress`  — someone has started: at least one answer, or at least
 *                     one recipient done but not all. A respondent halfway
 *                     through must not read as `waiting`.
 *  - `waiting`      — no answers yet, OR no recipients yet
 *
 * The `complete` check is evaluated first so a fully-progressed engagement
 * never reads as `waiting`. An engagement with zero recipients is `waiting`
 * (there's no one to complete it).
 */
export function engagementStatus(s: StatusCounts): EngagementStatus {
  if (s.recipients_count > 0 && s.completed_recipients === s.recipients_count) {
    return "complete";
  }
  if (
    s.recipients_count > 0 &&
    (s.completed_recipients > 0 || (s.answered_responses ?? 0) > 0)
  ) {
    return "in_progress";
  }
  return "waiting";
}

/** Human-readable label per status. */
export const STATUS_LABELS: Record<EngagementStatus, string> = {
  waiting: "Waiting",
  in_progress: "In progress",
  complete: "Complete",
};

/** Stable iteration order for option lists + rollups (most→least "done"
 * reads naturally as a rollup: "1 complete · 2 in progress · 3 waiting"). */
export const STATUS_ORDER: EngagementStatus[] = [
  "complete",
  "in_progress",
  "waiting",
];
