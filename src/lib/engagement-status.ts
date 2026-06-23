// Derived engagement status — single source of truth for the admin list.
//
// There is NO status column. Status is computed purely from response
// progress counts the API already returns (`answered_count`,
// `skipped_count`, `total_cards`). Keep this module pure + dependency-free
// so it stays unit-testable and reusable across the status pill, the
// per-client rollup, and the status filter/sort.

export type EngagementStatus = "waiting" | "in_progress" | "complete";

/** Minimal shape needed to derive status — a structural subset of
 * `EngagementSummary` so any object carrying the three counts works. */
export interface StatusCounts {
  answered_count: number;
  skipped_count: number;
  total_cards: number;
}

/**
 * Derive an engagement's status from its progress counts:
 *  - `complete`     — has cards AND every card is answered or skipped
 *                     (`total_cards > 0 && answered + skipped >= total_cards`)
 *  - `waiting`      — nothing answered or skipped yet (`answered + skipped === 0`)
 *  - `in_progress`  — anything in between (partial progress)
 *
 * The `complete` check is evaluated first so a fully-progressed engagement
 * never reads as `waiting`. An engagement with zero cards is `waiting`
 * (there's nothing to complete).
 */
export function engagementStatus(s: StatusCounts): EngagementStatus {
  const done = s.answered_count + s.skipped_count;
  if (s.total_cards > 0 && done >= s.total_cards) return "complete";
  if (done === 0) return "waiting";
  return "in_progress";
}

/** Human-readable label per status. */
export const STATUS_LABELS: Record<EngagementStatus, string> = {
  waiting: "Waiting",
  in_progress: "In progress",
  complete: "Complete",
};

/** CSS class suffix per status — paired with the `.status-pill` base in
 * `admin.css` (e.g. `status-pill status-pill--complete`). */
export const STATUS_CSS_CLASS: Record<EngagementStatus, string> = {
  waiting: "status-pill--waiting",
  in_progress: "status-pill--in-progress",
  complete: "status-pill--complete",
};

/** Stable iteration order for option lists + rollups (most→least "done"
 * reads naturally as a rollup: "1 complete · 2 in progress · 3 waiting"). */
export const STATUS_ORDER: EngagementStatus[] = [
  "complete",
  "in_progress",
  "waiting",
];
