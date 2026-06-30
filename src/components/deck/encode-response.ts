import type { ResponseState } from "@/lib/api";

// The action the recipient took on a card, awaiting a save. Mirrors the
// PendingAction union dispatched by the handlers in src/scripts/app.ts.
export type PendingAction =
  | { kind: "confirm" }
  | { kind: "edit"; correction: string }
  | { kind: "skip"; note?: string }
  | { kind: "single-select"; option: string; note?: string }
  | { kind: "multi-select"; options: string[]; note?: string }
  | { kind: "text"; text: string; note?: string }
  | { kind: "link"; url: string; note?: string }
  | {
      kind: "contact";
      name: string;
      email: string;
      role: string;
      note?: string;
    }
  | { kind: "files-continue"; note?: string };

export interface EncodedResponse {
  state: ResponseState;
  response_value: unknown;
}

// Fold an optional free-form note into the structured value. null is preserved
// (skip with no note); objects get a note field. Identical to app.ts withNote.
function withNote(v: unknown, note?: string): unknown {
  if (!note) return v;
  if (v === null) return { note };
  if (typeof v === "object" && v !== null) return { ...v, note };
  return v;
}

/**
 * Encode a pending action into the `{ state, response_value }` the backend
 * expects. Mirrors performSave() in src/scripts/app.ts exactly — the backend
 * derives engagement_id from the token and stamps answered_at/viewed_at, so we
 * only ship state + response_value.
 *
 * @param fileIds  upload ids for the current card (file-upload cards)
 * @param hasVoice whether the card has a saved voice upload — a voice note
 *   promotes a non-answered state to "answered" (a voice-only answer counts).
 */
export function encodeResponse(
  action: PendingAction,
  fileIds: string[] = [],
  hasVoice = false,
): EncodedResponse {
  let state: ResponseState;
  let value: unknown;

  switch (action.kind) {
    case "confirm":
      state = "answered";
      value = { confirmed: true };
      break;
    case "edit":
      state = "answered";
      value = { confirmed: false, correction: action.correction };
      break;
    case "skip":
      state = "skipped";
      value = withNote(null, action.note);
      break;
    case "single-select":
      state = "answered";
      value = withNote({ selected: action.option }, action.note);
      break;
    case "multi-select":
      state = "answered";
      value = withNote({ selected: action.options }, action.note);
      break;
    case "text":
      state = "answered";
      value = withNote({ text: action.text }, action.note);
      break;
    case "link":
      state = "answered";
      value = withNote({ url: action.url }, action.note);
      break;
    case "contact":
      state = "answered";
      value = withNote(
        { name: action.name, email: action.email, role: action.role },
        action.note,
      );
      break;
    case "files-continue": {
      const hasFiles = fileIds.length > 0;
      const hasNote = !!action.note;
      state = hasFiles || hasNote ? "answered" : "skipped";
      const base = hasFiles ? { file_ids: fileIds } : null;
      value = withNote(base, action.note);
      break;
    }
  }

  // A voice recording supplements the answer: a voice-only answer (no typed
  // value / no selection) still counts as answered.
  if (hasVoice && state !== "answered") {
    state = "answered";
  }

  return { state, response_value: value };
}
