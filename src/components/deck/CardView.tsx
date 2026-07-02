import { FileText } from "lucide-react";

import type { Card as CardModel, ClientResponse, UploadRow } from "@/lib/api";
import { Button } from "@/components/ui/button";

import { ResumeBanner, SaveBanner, TopBar } from "./chrome";
import { FileUploadInput } from "./FileUpload";
import type { DeckHandlers } from "./handlers";
import {
  ConfirmEditView,
  ContactShareInput,
  DocumentLinkInput,
  EditBody,
  MultiSelectInput,
  SingleSelectInput,
  TextInput,
} from "./inputs";
import { VoiceRecorder } from "./VoiceRecorder";

/** File/voice data + callbacks for the current card. */
interface CardMedia {
  token: string;
  voiceEnabled: boolean;
  cardFiles: UploadRow[];
  voiceUpload?: UploadRow;
  onFileUploaded: (row: UploadRow) => void;
  onFileRemoved: (uploadId: string) => void;
  onVoiceSaved: (row: UploadRow) => void;
  onVoiceDeleted: () => void;
}

function PriorHint({
  card,
  existing,
}: {
  card: CardModel;
  existing?: ClientResponse;
}): React.ReactElement | null {
  if (!existing) return null;
  const v = (existing.response_value ?? {}) as { confirmed?: boolean };
  let text: string | null = null;
  if (existing.state === "skipped") {
    text = "You skipped this earlier. Answer if you want to revisit.";
  } else if (existing.state === "answered") {
    if (card.response_type === "confirm-edit") {
      text = v.confirmed ? "You confirmed this earlier." : "You sent edits earlier.";
    } else {
      text = "Your previous answer is loaded. Edit and resubmit to update it.";
    }
  }
  if (!text) return null;
  return (
    <div className="mt-4 rounded-md bg-secondary px-3 py-2 text-sm font-medium text-secondary-foreground">
      {text}
    </div>
  );
}

function InputForType({
  card,
  mode,
  saving,
  existing,
  handlers,
  media,
}: {
  card: CardModel;
  mode: "view" | "edit" | "saving";
  saving: boolean;
  existing?: ClientResponse;
  handlers: DeckHandlers;
  media: CardMedia;
}): React.ReactElement | null {
  if (card.response_type === "confirm-edit") {
    if (mode === "edit") {
      return (
        <EditBody
          card={card}
          saving={saving}
          existing={existing}
          onSubmit={handlers.onEditSubmit}
          onCancel={handlers.onEditCancel}
        />
      );
    }
    return (
      <ConfirmEditView
        card={card}
        saving={saving}
        onConfirm={handlers.onConfirm}
        onEditStart={handlers.onEditStart}
        onSkip={() => handlers.onSkip()}
      />
    );
  }

  switch (card.response_type) {
    case "single-select":
      return (
        <SingleSelectInput
          card={card}
          saving={saving}
          existing={existing}
          onSelect={handlers.onSingleSelect}
          onSkip={handlers.onSkip}
        />
      );
    case "multi-select":
      return (
        <MultiSelectInput
          card={card}
          saving={saving}
          existing={existing}
          onSubmit={handlers.onMultiSelectSubmit}
          onSkip={handlers.onSkip}
        />
      );
    case "short-text":
    case "long-text":
      return (
        <TextInput
          card={card}
          saving={saving}
          existing={existing}
          multiline={card.response_type === "long-text"}
          onSubmit={(text) => handlers.onTextSubmit(text)}
          onSkip={() => handlers.onSkip()}
        />
      );
    case "document-link":
      return (
        <DocumentLinkInput
          card={card}
          saving={saving}
          existing={existing}
          onSubmit={handlers.onLinkSubmit}
          onSkip={handlers.onSkip}
        />
      );
    case "contact-share":
      return (
        <ContactShareInput
          card={card}
          saving={saving}
          existing={existing}
          onSubmit={handlers.onContactSubmit}
          onSkip={handlers.onSkip}
        />
      );
    case "file-upload":
      return (
        <FileUploadInput
          card={card}
          saving={saving}
          token={media.token}
          existingFiles={media.cardFiles}
          hasVoice={!!media.voiceUpload}
          onUploaded={media.onFileUploaded}
          onRemoved={media.onFileRemoved}
          onContinue={handlers.onFilesContinue}
          onSkip={handlers.onSkip}
        />
      );
    default:
      return null;
  }
}

export function CardView({
  card,
  position,
  total,
  mode,
  saveError,
  showResume,
  existing,
  orgLogoSrc,
  orgName,
  handlers,
  media,
}: {
  card: CardModel;
  position: number;
  total: number;
  mode: "view" | "edit" | "saving";
  saveError: string | null;
  showResume: boolean;
  existing?: ClientResponse;
  orgLogoSrc?: string | null;
  orgName?: string | null;
  handlers: DeckHandlers;
  media: CardMedia;
}): React.ReactElement {
  const saving = mode === "saving";
  const showVoice = media.voiceEnabled && mode !== "edit";
  return (
    <div className="flex min-h-dvh flex-col">
      <TopBar
        position={position}
        total={total}
        orgLogoSrc={orgLogoSrc}
        orgName={orgName}
        onBack={handlers.onNavBack}
        onForward={handlers.onNavForward}
        onPicker={handlers.onPickerOpen}
        backDisabled={position <= 1}
        forwardDisabled={position >= total}
      />
      {saveError ? (
        <SaveBanner message={saveError} onRetry={handlers.onRetry} />
      ) : null}
      {showResume ? <ResumeBanner /> : null}
      <main className="flex flex-1 justify-center px-4 py-6">
        <article className="w-full max-w-xl" aria-labelledby="card-title">
          <p className="text-xs font-semibold uppercase tracking-wide text-primary">
            {card.category}
          </p>
          <h1
            id="card-title"
            className="mt-2 text-2xl font-bold tracking-tight text-foreground"
          >
            {card.title}
          </h1>
          <hr className="my-4 border-border" />
          <p className="text-[0.95rem] leading-relaxed text-muted-foreground">
            {card.context}
          </p>
          {card.attachment_path ? (
            <Button
              variant="outline"
              size="sm"
              type="button"
              onClick={handlers.onAttachmentOpen}
              className="mt-4"
            >
              <FileText />
              View Active Reference
            </Button>
          ) : null}
          <p className="mt-4 text-[1.05rem] font-semibold text-foreground">
            {card.question}
          </p>
          <PriorHint card={card} existing={existing} />
          <div className="mt-5">
            <InputForType
              card={card}
              mode={mode}
              saving={saving}
              existing={existing}
              handlers={handlers}
              media={media}
            />
          </div>
          {showVoice ? (
            <div className="mt-6 border-t border-border pt-5">
              <VoiceRecorder
                key={card.id}
                token={media.token}
                cardId={card.id}
                existingUpload={media.voiceUpload}
                disabled={saving}
                onSaved={media.onVoiceSaved}
                onDeleted={media.onVoiceDeleted}
              />
            </div>
          ) : null}
        </article>
      </main>
    </div>
  );
}
