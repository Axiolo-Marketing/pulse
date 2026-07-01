// The callback surface the card UI dispatches into — the React equivalent of
// the CardHandlers object wired from data-action attributes in app.ts. The
// DeckRunner builds a concrete instance backed by performSave + the reducer.
export interface DeckHandlers {
  onConfirm: () => void;
  onEditStart: () => void;
  onEditCancel: () => void;
  onEditSubmit: (correction: string) => void;
  onSingleSelect: (option: string, note?: string) => void;
  onMultiSelectSubmit: (options: string[], note?: string) => void;
  onTextSubmit: (text: string, note?: string) => void;
  onLinkSubmit: (url: string, note?: string) => void;
  onContactSubmit: (
    contact: { name: string; email: string; role: string },
    note?: string,
  ) => void;
  onFilesContinue: (note?: string) => void;
  onSkip: (note?: string) => void;
  onRetry: () => void;
  onNavBack: () => void;
  onNavForward: () => void;
  onNavJumpTo: (index: number) => void;
  onPickerOpen: () => void;
  onPickerClose: () => void;
  onAttachmentOpen: () => void;
  onAttachmentClose: () => void;
}
