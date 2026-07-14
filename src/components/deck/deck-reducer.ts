// UI state machine for the client card deck (the v2 port of the imperative
// closure in src/scripts/app.ts). Pure + framework-free so it can be unit
// tested exhaustively; the React island drives it via useReducer.
//
// `index` runs 0..total. index === total is the "all done" / complete state.
// Server data (responses/uploads) and the async save/retry orchestration live
// in the DeckRunner component — this reducer only owns navigation + UI flags.

/** `"waiting"`: reactive cards, right after a qualifying correction save —
 * the deck stays parked on the corrected card instead of advancing, showing
 * a quiet "reviewing your correction…" status while it waits (briefly) to
 * see if a follow-up lands. `CardView` renders no inputs/actions at all in
 * this mode (nothing to disable, nothing to double-submit). */
export type DeckMode = "view" | "edit" | "saving" | "waiting";

export interface DeckUiState {
  index: number;
  total: number;
  mode: DeckMode;
  saveError: string | null;
  /** Attachment ("Active Reference") modal. */
  modalOpen: boolean;
  /** Jump-to-card picker. */
  pickerOpen: boolean;
  /** "Welcome back" banner — shown once when a returning recipient boots
   * mid-deck, dismissed on the first navigation. */
  showResume: boolean;
}

export type DeckAction =
  | { type: "navigate"; index: number }
  | { type: "advance" }
  | { type: "saveStart" }
  | { type: "saveFailed"; message: string }
  | { type: "editStart" }
  | { type: "editCancel" }
  | { type: "openModal" }
  | { type: "closeModal" }
  | { type: "openPicker" }
  | { type: "closePicker" }
  /** Reactive cards: a qualifying correction was just saved and the deck is
   * waiting in place (not advancing) to see if it kicks off a follow-up —
   * see `awaitFollowUp`/`pullInFollowUpAndNavigate` in DeckApp.tsx. Leaves
   * `index` untouched (still the corrected card); only `mode` changes. */
  | { type: "awaitFollowUp" }
  /** Reactive cards: `count` cards were just spliced into the live deck
   * array (by the caller, alongside a matching `setCards`) starting at
   * `insertAt`. Grows `total` to match. If the respondent was on the
   * complete screen (`index === total`, the old total) when the cards
   * landed, jumps them straight to the new follow-up with per-card UI
   * state reset — otherwise `index` is left untouched, since
   * `insertAt` never lands at or before the respondent's current
   * position (see `spliceIndexFor` in `src/lib/deck-order.ts`). Used only
   * by the BACKGROUND poll fallback (`pollForFollowUp`) — the in-place wait
   * uses `followUpReady` below instead, which always navigates in. */
  | { type: "cardsInserted"; insertAt: number; count: number }
  /** Reactive cards: the in-place wait (`awaitFollowUp`) resolved with a
   * follow-up ready inside its budget. Unlike `cardsInserted`, this always
   * navigates straight to `insertAt` with a full per-card UI state reset —
   * the respondent was still parked on the waiting card by construction
   * (the caller checked before dispatching), so there's no "leave index
   * untouched" case to consider here. */
  | { type: "followUpReady"; insertAt: number; count: number };

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

export function initialDeckState(
  bootIndex: number,
  total: number,
): DeckUiState {
  const index = clamp(bootIndex, 0, total);
  return {
    index,
    total,
    mode: "view",
    saveError: null,
    modalOpen: false,
    pickerOpen: false,
    showResume: index > 0 && index < total,
  };
}

// Every navigation resets per-card UI state, mirroring navigateTo() in app.ts.
function navTo(state: DeckUiState, index: number): DeckUiState {
  return {
    ...state,
    index: clamp(index, 0, state.total),
    mode: "view",
    saveError: null,
    modalOpen: false,
    pickerOpen: false,
    showResume: false,
  };
}

export function deckReducer(
  state: DeckUiState,
  action: DeckAction,
): DeckUiState {
  switch (action.type) {
    case "navigate":
      return navTo(state, action.index);
    case "advance":
      return navTo(state, state.index + 1);
    case "saveStart":
      return { ...state, mode: "saving", saveError: null };
    case "saveFailed":
      return { ...state, mode: "view", saveError: action.message };
    case "editStart":
      return { ...state, mode: "edit", saveError: null };
    case "editCancel":
      return { ...state, mode: "view" };
    case "openModal":
      return { ...state, modalOpen: true };
    case "closeModal":
      return { ...state, modalOpen: false };
    case "openPicker":
      return { ...state, pickerOpen: true };
    case "closePicker":
      return { ...state, pickerOpen: false };
    case "awaitFollowUp":
      return { ...state, mode: "waiting", saveError: null };
    case "followUpReady": {
      const total = state.total + action.count;
      return {
        ...state,
        total,
        index: action.insertAt,
        mode: "view",
        saveError: null,
        modalOpen: false,
        pickerOpen: false,
        showResume: false,
      };
    }
    case "cardsInserted": {
      const wasComplete = state.index === state.total;
      const total = state.total + action.count;
      if (!wasComplete) return { ...state, total };
      return {
        ...state,
        total,
        index: action.insertAt,
        mode: "view",
        saveError: null,
        modalOpen: false,
        pickerOpen: false,
        showResume: false,
      };
    }
    default:
      return state;
  }
}
