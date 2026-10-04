// Pure helpers behind the admin engagement list: per-client rollup text,
// sort comparators, and the persisted filter state. Kept free of React so
// they unit-test directly.

import type { EngagementSummary } from "./api";
import {
  engagementStatus,
  STATUS_LABELS,
  STATUS_ORDER,
  type EngagementStatus,
} from "./engagement-status";

export type SortKey = "name" | "last_active" | "status";

const UNTITLED = "Untitled engagement";

function nameOf(s: EngagementSummary): string {
  return s.engagement_name?.trim() || UNTITLED;
}

function byName(a: EngagementSummary, b: EngagementSummary): number {
  return nameOf(a).localeCompare(nameOf(b), undefined, { sensitivity: "base" });
}

/** Concise per-client rollup of derived statuses, e.g.
 * "3 engagements · 1 complete · 2 in progress". Zero buckets are omitted; an
 * empty client reads just "0 engagements". Pass the client's FULL set of
 * engagements (pre-filter) so the rollup describes the client, not the
 * current filtered view. */
export function clientRollupText(members: EngagementSummary[]): string {
  const counts: Record<EngagementStatus, number> = {
    complete: 0,
    in_progress: 0,
    waiting: 0,
  };
  for (const m of members) counts[engagementStatus(m)] += 1;
  const parts = [
    `${members.length} engagement${members.length === 1 ? "" : "s"}`,
  ];
  for (const status of STATUS_ORDER) {
    const n = counts[status];
    if (n > 0) parts.push(`${n} ${STATUS_LABELS[status].toLowerCase()}`);
  }
  return parts.join(" · ");
}

/** Comparator for a sort key, applied within each client section. Name sort
 * is case-insensitive with an "Untitled engagement" fallback; last-active is
 * newest first with never-active last; status orders complete → in progress
 * → waiting, tie-broken by name. */
export function sortComparator(
  key: SortKey,
): (a: EngagementSummary, b: EngagementSummary) => number {
  if (key === "name") return byName;
  if (key === "last_active") {
    return (a, b) => {
      const ta = a.last_active_at ? Date.parse(a.last_active_at) : -Infinity;
      const tb = b.last_active_at ? Date.parse(b.last_active_at) : -Infinity;
      if (ta === tb) return 0;
      return tb > ta ? 1 : -1;
    };
  }
  const rank = (s: EngagementSummary): number =>
    STATUS_ORDER.indexOf(engagementStatus(s));
  return (a, b) => rank(a) - rank(b) || byName(a, b);
}

// ── Persisted filters ──
// Module-level state so going to a detail page and back keeps the operator's
// view (the list component unmounts on navigation). Backed by sessionStorage
// so it also survives a reload within the tab.

export interface ListControls {
  query: string;
  status: "all" | EngagementStatus;
  client: string;
  owner: string;
  sort: SortKey;
}

export const DEFAULT_CONTROLS: ListControls = {
  query: "",
  status: "all",
  client: "all",
  owner: "all",
  sort: "name",
};

const STORAGE_KEY = "pulse.admin.listControls";
let current: ListControls | null = null;

export function loadListControls(): ListControls {
  if (current) return current;
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (raw) {
      current = { ...DEFAULT_CONTROLS, ...(JSON.parse(raw) as Partial<ListControls>) };
      return current;
    }
  } catch {
    // storage blocked or corrupt — fall through to defaults
  }
  current = { ...DEFAULT_CONTROLS };
  return current;
}

export function saveListControls(next: ListControls): void {
  current = next;
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // best effort
  }
}

/** Test hook: drop the in-memory copy so storage is re-read. */
export function resetListControlsCache(): void {
  current = null;
}
