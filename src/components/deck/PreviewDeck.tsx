import { useReducer, useRef, useState } from "react";

import type { Card as CardModel, ClientResponse } from "@/lib/api";

import { AttachmentModal } from "./AttachmentModal";
import { CardPicker } from "./CardPicker";
import { CardView, type CardMedia } from "./CardView";
import { deckReducer, initialDeckState } from "./deck-reducer";
import { encodeResponse, type PendingAction } from "./encode-response";
import type { DeckHandlers } from "./handlers";
import { CompleteCard } from "./states";

/** The respondent deck, driven entirely in memory — for the operator's
 * preview before sending. Same screens as `DeckApp` (CardView, picker,
 * attachment modal, complete card), but every "save" just records the
 * answer locally and advances: nothing is sent to the server, no
 * respondent progress changes, and no AI follow-ups are generated. Voice
 * and file uploads render inert (`media.preview`). */
export function PreviewDeck({
  cards,
  voiceEnabled,
  orgLogoSrc,
  orgName,
}: {
  cards: CardModel[];
  voiceEnabled: boolean;
  orgLogoSrc: string | null;
  orgName: string | null;
}): React.ReactElement {
  const total = cards.length;
  const [ui, dispatch] = useReducer(deckReducer, undefined, () =>
    initialDeckState(0, total),
  );
  const [responses, setResponses] = useState<Map<string, ClientResponse>>(
    () => new Map(),
  );
  const indexRef = useRef(ui.index);
  indexRef.current = ui.index;

  const card = ui.index < total ? cards[ui.index] : null;

  function record(action: PendingAction): void {
    const current = cards[indexRef.current];
    if (!current) return;
    const { state, response_value } = encodeResponse(action, [], false);
    const now = new Date().toISOString();
    setResponses((m) =>
      new Map(m).set(current.id, {
        id: `preview-${current.id}`,
        card_id: current.id,
        engagement_id: current.engagement_id,
        recipient_id: "preview",
        state,
        response_value,
        viewed_at: now,
        answered_at: now,
        created_at: now,
        updated_at: now,
      }),
    );
    dispatch({ type: "advance" });
  }

  const handlers: DeckHandlers = {
    onConfirm: () => record({ kind: "confirm" }),
    onEditStart: () => dispatch({ type: "editStart" }),
    onEditCancel: () => dispatch({ type: "editCancel" }),
    onEditSubmit: (correction) => record({ kind: "edit", correction }),
    onSingleSelect: (option, note) =>
      record({ kind: "single-select", option, note }),
    onNoteSubmit: (note, option) => record({ kind: "note", note, option }),
    onMultiSelectSubmit: (options, note) =>
      record({ kind: "multi-select", options, note }),
    onTextSubmit: (text, note) => record({ kind: "text", text, note }),
    onLinkSubmit: (url, note) => record({ kind: "link", url, note }),
    onContactSubmit: (contact, note) =>
      record({ kind: "contact", ...contact, note }),
    onFilesContinue: (note) => record({ kind: "files-continue", note }),
    onSkip: (note) => record({ kind: "skip", note }),
    onRetry: () => undefined,
    onNavBack: () => dispatch({ type: "navigate", index: indexRef.current - 1 }),
    onNavForward: () =>
      dispatch({ type: "navigate", index: indexRef.current + 1 }),
    onNavJumpTo: (index) => dispatch({ type: "navigate", index }),
    onPickerOpen: () => dispatch({ type: "openPicker" }),
    onPickerClose: () => dispatch({ type: "closePicker" }),
    onAttachmentOpen: () => dispatch({ type: "openModal" }),
    onAttachmentClose: () => dispatch({ type: "closeModal" }),
  };

  if (!card) {
    return (
      <CompleteCard
        name={null}
        onReview={
          total > 0
            ? () => dispatch({ type: "navigate", index: total - 1 })
            : undefined
        }
      />
    );
  }

  const media: CardMedia = {
    preview: true,
    token: "",
    voiceEnabled,
    cardFiles: [],
    voiceUpload: undefined,
    onFileUploaded: () => undefined,
    onFileRemoved: () => undefined,
    onVoiceSaved: () => undefined,
    onVoiceDeleted: () => undefined,
  };

  return (
    <>
      <CardView
        card={card}
        position={ui.index + 1}
        total={total}
        mode={ui.mode}
        saveError={null}
        showResume={false}
        existing={responses.get(card.id)}
        orgLogoSrc={orgLogoSrc}
        orgName={orgName}
        handlers={handlers}
        media={media}
        outline={{
          cards,
          responses,
          currentIndex: ui.index,
          onJump: handlers.onNavJumpTo,
        }}
      />
      {ui.pickerOpen ? (
        <CardPicker
          cards={cards}
          responses={responses}
          currentIndex={ui.index}
          onJump={handlers.onNavJumpTo}
          onClose={handlers.onPickerClose}
        />
      ) : null}
      {ui.modalOpen && card.attachment_path ? (
        <AttachmentModal
          title={card.title}
          path={card.attachment_path}
          onClose={handlers.onAttachmentClose}
        />
      ) : null}
    </>
  );
}
