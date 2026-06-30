// UI state machine for the client card deck (the v2 port of the imperative
// closure in src/scripts/app.ts). Pure + framework-free so it can be unit
// tested exhaustively; the React island drives it via useReducer.
//
// `index` runs 0..total. index === total is the "all done" / complete state.
// Server data (responses/uploads) and the async save/retry orchestration live
// in the DeckRunner component — this reducer only owns navigation + UI flags.

export type DeckMode = "view" | "edit" | "saving";

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
  | { type: "closePicker" };

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
    default:
      return state;
  }
}
