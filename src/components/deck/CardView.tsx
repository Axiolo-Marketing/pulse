import { useEffect } from "react";
import {
  ChevronLeft,
  ChevronRight,
  FileText,
  History,
  LoaderCircle,
  Mic,
  Upload,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DESKTOP_QUERY,
  FINE_POINTER_QUERY,
  WIDE_QUERY,
  useMediaQuery,
} from "@/lib/use-media-query";
import { cn } from "@/lib/utils";

import type { Card as CardModel, ClientResponse, UploadRow } from "@/lib/api";

import { DeckNav, ResumeBanner, SaveBanner, TopBar } from "./chrome";
import { DeckOutline, type DeckOutlineData } from "./DeckOutline";
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
import { ReferencePane } from "./ReferencePane";
import { VoiceRecorder } from "./VoiceRecorder";

/** File/voice data + callbacks for the current card. */
export interface CardMedia {
  /** Admin deck preview: nothing is uploaded or recorded — the voice and
   * file controls render as they would, but inert, with a note saying so. */
  preview?: boolean;
  token: string;
  voiceEnabled: boolean;
  cardFiles: UploadRow[];
  voiceUpload?: UploadRow;
  onFileUploaded: (row: UploadRow) => void;
  onFileRemoved: (uploadId: string) => void;
  onVoiceSaved: (row: UploadRow) => void;
  onVoiceDeleted: () => void;
}

// Reactive cards: shown in place of the normal answer body right after a
// qualifying correction save, while DeckApp waits (briefly) to see if the
// correction kicked off a follow-up — see `awaitFollowUp` in DeckApp.tsx. No
// actions render at all here (nothing to disable, nothing to double-submit);
// navigation (back/forward/picker) stays live in the footer/topbar and
// cancels the wait if the respondent uses it.
function WaitingStatus(): React.ReactElement {
  return (
    <div
      role="status"
      className="flex items-center gap-3 rounded-lg border border-border bg-muted/50 px-4 py-3 text-sm text-foreground"
    >
      <LoaderCircle
        className="size-4 shrink-0 animate-spin text-muted-foreground"
        aria-hidden="true"
      />
      One moment — reviewing your correction…
    </div>
  );
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
    <div className="mt-4 flex items-center gap-2 rounded-lg border border-border bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
      <History className="size-4 shrink-0" aria-hidden="true" />
      {text}
    </div>
  );
}

/** True when a key press belongs to a form field, not deck navigation. */
function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  if (t.isContentEditable) return true;
  if (t.closest('[role="dialog"]')) return true;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/** A small keyboard-key badge for shortcut hints. */
export function Kbd({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-border bg-muted px-1 font-sans text-[11px] font-medium text-muted-foreground">
      {children}
    </kbd>
  );
}

/** Primary + optional skip buttons for preview-only stand-ins, styled like
 * the real inputs' action rows. */
function PreviewActions({
  card,
  primary,
  onPrimary,
  onSkip,
}: {
  card: CardModel;
  primary: string;
  onPrimary: () => void;
  onSkip: () => void;
}): React.ReactElement {
  return (
    <div className="mt-3 flex flex-col gap-2 [&>button]:h-11 [&>button]:text-[0.95rem]">
      <Button type="button" onClick={onPrimary}>
        {primary}
      </Button>
      {card.skip_allowed ? (
        <Button
          variant="ghost"
          type="button"
          onClick={onSkip}
          className="text-muted-foreground"
        >
          Skip for now
        </Button>
      ) : null}
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
          onNoteSubmit={handlers.onNoteSubmit}
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
      if (media.preview) {
        return (
          <div className="flex flex-col gap-3">
            <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed border-border px-4 py-6 text-center">
              <Upload className="size-5 text-muted-foreground" aria-hidden="true" />
              <p className="text-sm font-medium text-foreground">Upload files</p>
              <p className="text-xs text-muted-foreground">
                Respondents can attach files here. Uploads are off in preview.
              </p>
            </div>
            <PreviewActions
              card={card}
              primary="Continue"
              onPrimary={() => handlers.onFilesContinue()}
              onSkip={() => handlers.onSkip()}
            />
          </div>
        );
      }
      return (
        <FileUploadInput
          card={card}
          saving={saving}
          token={media.token}
          existing={existing}
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
  outline,
  pollActive = false,
}: {
  card: CardModel;
  position: number;
  total: number;
  mode: "view" | "edit" | "saving" | "waiting";
  saveError: string | null;
  showResume: boolean;
  existing?: ClientResponse;
  orgLogoSrc?: string | null;
  orgName?: string | null;
  handlers: DeckHandlers;
  media: CardMedia;
  /** Desktop question outline (sidebar). Omit to never show it. */
  outline?: DeckOutlineData;
  /** A background follow-up poll is running; shows a quiet status line. */
  pollActive?: boolean;
}): React.ReactElement {
  const saving = mode === "saving";
  const isDesktop = useMediaQuery(DESKTOP_QUERY);
  const isWide = useMediaQuery(WIDE_QUERY);
  const hasKeyboard = useMediaQuery(FINE_POINTER_QUERY);
  const showOutline = isDesktop && !!outline;
  const showSidePane = isWide && !!card.attachment_path;

  // ← / → move between questions on any device with a keyboard. Ignored
  // while typing, with a modifier held, or when a dialog has focus.
  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTypingTarget(e.target)) return;
      if (e.key === "ArrowLeft" && position > 1) {
        e.preventDefault();
        handlers.onNavBack();
      } else if (e.key === "ArrowRight" && position < total) {
        e.preventDefault();
        handlers.onNavForward();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handlers, position, total]);
  // A new card starts at the top — otherwise advancing from a long card
  // (scrolled down to reach its actions) lands mid-way into the next one,
  // with its title hidden under the sticky header.
  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [card.id]);
  const showVoice = media.voiceEnabled && mode !== "edit" && mode !== "waiting";
  return (
    <div className="flex min-h-dvh flex-col">
      <TopBar
        position={position}
        total={total}
        orgLogoSrc={orgLogoSrc}
        orgName={orgName}
        wide={showOutline}
      />
      {pollActive ? (
        <p
          role="status"
          className="mx-auto w-full max-w-xl px-5 pt-3 text-center text-xs text-muted-foreground"
        >
          Checking if we need a quick follow-up…
        </p>
      ) : null}
      {saveError ? (
        <SaveBanner message={saveError} onRetry={handlers.onRetry} />
      ) : null}
      {showResume ? <ResumeBanner /> : null}
      <div className="mx-auto flex w-full max-w-7xl flex-1">
      {showOutline ? (
        <aside className="sticky top-14 h-[calc(100dvh-3.5rem)] w-72 shrink-0 border-r border-border">
          <DeckOutline {...outline} />
        </aside>
      ) : null}
      <div className="flex min-w-0 flex-1 flex-col">
      <main
        className={cn(
          "flex min-w-0 flex-1 justify-center px-5 pt-8 pb-10",
          showOutline && "px-10 pt-12",
          showSidePane && "gap-10",
        )}
      >
        <article
          className={cn("w-full max-w-xl", showSidePane && "max-w-md shrink-0")}
          aria-labelledby="card-title"
        >
          {card.category ? (
            <p className="inline-flex rounded-full border border-border px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
              {card.category}
            </p>
          ) : null}
          <h1
            id="card-title"
            className="mt-3 text-[1.75rem] font-semibold leading-tight tracking-tight text-foreground text-balance"
          >
            {card.title}
          </h1>
          {card.context ? (
            <p className="mt-3 text-[0.95rem] leading-relaxed text-muted-foreground">
              {card.context}
            </p>
          ) : null}
          {card.attachment_path && !showSidePane ? (
            <button
              type="button"
              onClick={handlers.onAttachmentOpen}
              className="mt-4 flex w-full items-center gap-3 rounded-lg border border-border px-3.5 py-3 text-left transition-colors hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
            >
              <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                <FileText className="size-4" aria-hidden="true" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-foreground">
                  Reference document
                </span>
                <span className="block text-xs text-muted-foreground">
                  Tap to open alongside this question
                </span>
              </span>
              <ChevronRight className="size-4 text-muted-foreground" aria-hidden="true" />
            </button>
          ) : null}
          <p className="mt-6 text-lg font-semibold leading-snug text-foreground">
            {card.question}
          </p>
          <PriorHint card={card} existing={existing} />
          <div className="mt-4">
            {mode === "waiting" ? (
              <WaitingStatus />
            ) : (
              // Keyed by card so draft state (typed note/text, local
              // selections) resets when the deck advances — consecutive
              // cards of the same type would otherwise reuse the same
              // component instance and inherit the previous card's drafts.
              <InputForType
                key={card.id}
                card={card}
                mode={mode}
                saving={saving}
                existing={existing}
                handlers={handlers}
                media={media}
              />
            )}
          </div>
          {showVoice && media.preview ? (
            <div className="mt-8 border-t border-border pt-5">
              <Button
                variant="ghost"
                size="sm"
                type="button"
                disabled
                className="-ml-2 text-muted-foreground"
              >
                <Mic />
                Add a voice note
              </Button>
              <p className="mt-1 text-xs text-muted-foreground">
                Respondents can record here. Recording is off in preview.
              </p>
            </div>
          ) : showVoice ? (
            <div className="mt-8 border-t border-border pt-5">
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
        {showSidePane && card.attachment_path ? (
          <div className="sticky top-24 h-[calc(100dvh-12rem)] min-w-0 flex-1">
            <ReferencePane title={card.title} path={card.attachment_path} />
          </div>
        ) : null}
      </main>
      {isDesktop ? (
        // Always-visible desktop navigation, pinned to the bottom of the
        // question area (the outline is for jumping; this is the flow).
        <nav
          aria-label="Card navigation"
          className="sticky bottom-0 z-10 border-t border-border bg-background/90 backdrop-blur"
        >
          <div
            className={cn(
              "mx-auto flex h-16 w-full items-center justify-between gap-4 px-10",
              showSidePane ? "max-w-none" : "max-w-[calc(36rem+5rem)]",
            )}
          >
            <Button
              variant="outline"
              onClick={handlers.onNavBack}
              disabled={position <= 1}
              className="h-10 min-w-32 gap-1.5"
            >
              <ChevronLeft />
              Previous
            </Button>
            <div className="flex flex-col items-center gap-1 text-xs text-muted-foreground">
              <span className="text-sm font-medium tabular-nums text-foreground">
                Question {position} of {total}
              </span>
              {hasKeyboard ? (
                <span className="flex items-center gap-1">
                  <Kbd>←</Kbd>
                  <Kbd>→</Kbd>
                  <span className="ml-0.5">keys work too</span>
                </span>
              ) : null}
            </div>
            <Button
              variant="outline"
              onClick={handlers.onNavForward}
              disabled={position >= total}
              className="h-10 min-w-32 gap-1.5"
            >
              Next
              <ChevronRight />
            </Button>
          </div>
        </nav>
      ) : null}
      </div>
      </div>
      <footer className="sticky bottom-0 z-10 border-t border-border bg-background/90 py-2.5 pb-[max(0.625rem,env(safe-area-inset-bottom))] backdrop-blur lg:hidden">
        <DeckNav
          position={position}
          total={total}
          onBack={handlers.onNavBack}
          onForward={handlers.onNavForward}
          onPicker={handlers.onPickerOpen}
          backDisabled={position <= 1}
          forwardDisabled={position >= total}
        />
      </footer>
    </div>
  );
}
