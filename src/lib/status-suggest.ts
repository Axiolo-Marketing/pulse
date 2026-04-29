import type { Card, ClientResponse } from "./supabase";

// ClickUp status values per spec §14.3.
export const STATUS_VALUES = [
  "Waiting on IGTMS",
  "Waiting on Good Life",
  "Needs Attention",
  "IGTMS Review",
  "Client Review",
  "Blocked",
  "Approved",
  "Complete",
] as const;

export type Status = (typeof STATUS_VALUES)[number];

interface ResponseValueGuess {
  confirmed?: boolean;
  correction?: string;
  selected?: string | string[];
  text?: string;
  url?: string;
  name?: string;
  email?: string;
  role?: string;
  file_ids?: string[];
}

const NEEDS_HELP = /(need .*help|i need help|cannot|can not|unable)/i;
const BLOCKED = /(blocked|stuck|waiting on)/i;
const DONE_OPTION = /(done|approved|complete|in use|in place)/i;

// suggestStatus picks a default ClickUp status based on the spec's mapping.
// Tom can override via dropdown before copying.
export function suggestStatus(
  card: Card,
  response: ClientResponse | undefined
): Status {
  if (!response) return "Waiting on Good Life"; // never seen
  if (response.state === "not_started" || response.state === "viewed") {
    return "Waiting on Good Life";
  }
  if (response.state === "skipped") return "Waiting on Good Life";

  const v = (response.response_value ?? {}) as ResponseValueGuess;

  switch (card.response_type) {
    case "confirm-edit":
      // Confirmed verbatim or edited — both go to IGTMS Review.
      return "IGTMS Review";

    case "single-select": {
      const sel = (v.selected as string | undefined) ?? "";
      if (NEEDS_HELP.test(sel)) return "Needs Attention";
      if (BLOCKED.test(sel)) return "Blocked";
      if (DONE_OPTION.test(sel)) return "Approved";
      return "IGTMS Review";
    }

    case "multi-select": {
      const arr = Array.isArray(v.selected) ? v.selected : [];
      if (arr.length === 0) return "Waiting on Good Life";
      if (arr.some((s) => NEEDS_HELP.test(s))) return "Needs Attention";
      if (arr.some((s) => BLOCKED.test(s))) return "Blocked";
      return "IGTMS Review";
    }

    case "short-text":
    case "long-text": {
      const text = (v.text as string | undefined) ?? "";
      if (NEEDS_HELP.test(text)) return "Needs Attention";
      if (BLOCKED.test(text)) return "Blocked";
      return "IGTMS Review";
    }

    case "document-link":
      return v.url ? "IGTMS Review" : "Waiting on Good Life";

    case "contact-share":
      return v.email ? "IGTMS Review" : "Waiting on Good Life";

    case "file-upload": {
      const ids = v.file_ids ?? [];
      return ids.length > 0 ? "IGTMS Review" : "Waiting on Good Life";
    }

    default:
      return "IGTMS Review";
  }
}
