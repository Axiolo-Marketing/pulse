import { useEffect, useReducer, useRef, useState } from "react";
import { toast } from "sonner";

import { Toaster } from "@/components/ui/sonner";
import {
  clientApi,
  type Card as CardModel,
  type ClientResponse,
  type Engagement,
  type UploadRow,
} from "@/lib/api";
import { applyBranding } from "@/lib/branding";
import {
  hasTriggerPotential,
  orderDeckCards,
  runPoll,
  spliceIndexFor,
  waitSchedule,
} from "@/lib/deck-order";

import { AttachmentModal } from "./AttachmentModal";
import { CardPicker } from "./CardPicker";
import { CardView } from "./CardView";
import { deckReducer, initialDeckState } from "./deck-reducer";
import { encodeResponse, type PendingAction } from "./encode-response";
import type { DeckHandlers } from "./handlers";
import { CompleteCard, DeckError, DeckLoading } from "./states";

interface ReadyBoot {
  engagement: Engagement;
  cards: CardModel[];
  responses: ClientResponse[];
  uploads: UploadRow[];
  bootIndex: number;
  orgLogoSrc: string | null;
}

type BootState =
  | { status: "loading" }
  | { status: "error"; title: string; body: string }
  | { status: "ready"; token: string; data: ReadyBoot };

/** First card not yet answered/skipped, or `cards.length` (complete). */
function computeBootIndex(
  cards: CardModel[],
  responses: ClientResponse[],
): number {
  const byId = new Map(responses.map((r) => [r.card_id, r]));
  for (let i = 0; i < cards.length; i++) {
    const r = byId.get(cards[i].id);
    if (!r || (r.state !== "answered" && r.state !== "skipped")) return i;
  }
  return cards.length;
}

export default function DeckApp(): React.ReactElement {
  const [boot, setBoot] = useState<BootState>({ status: "loading" });

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("t");
    if (!token) {
      setBoot({
        status: "error",
        title: "This link is missing a code",
        body: "Please check the link your consultant sent you.",
      });
      return;
    }
    let cancelled = false;
    let logoUrl: string | null = null;
    (async () => {
      try {
        const engagement = await clientApi.me(token);
        const [rawCards, responses, uploads] = await Promise.all([
          clientApi.cards(token),
          clientApi.responses(token),
          clientApi.uploads(token),
        ]);
        if (cancelled) return;
        applyBranding(engagement.org_branding);
        logoUrl = await clientApi.logoObjectUrl(token);
        if (cancelled) return;
        // Reactive cards: an AI-generated follow-up is appended at the end
        // of the deck server-side. Re-order for display so it shows up
        // directly after the card whose correction triggered it, even on a
        // fresh page load — see src/lib/deck-order.ts.
        const cards = orderDeckCards(rawCards, responses);
        setBoot({
          status: "ready",
          token,
          data: {
            engagement,
            cards,
            responses,
            uploads,
            bootIndex: computeBootIndex(cards, responses),
            orgLogoSrc: logoUrl,
          },
        });
      } catch {
        if (!cancelled) {
          setBoot({
            status: "error",
            title: "We could not find your engagement",
            body: "Please check the link or contact your consultant.",
          });
        }
      }
    })();
    return () => {
      cancelled = true;
      if (logoUrl) URL.revokeObjectURL(logoUrl);
    };
  }, []);

  if (boot.status === "loading") return <DeckLoading />;
  if (boot.status === "error") {
    return <DeckError title={boot.title} body={boot.body} />;
  }
  // The Toaster mounts here (not in DeckRunner) so a toast fired right
  // before the complete screen replaces the card view still gets rendered.
  return (
    <>
      <DeckRunner token={boot.token} boot={boot.data} />
      <Toaster position="top-center" />
    </>
  );
}

function DeckRunner({
  token,
  boot,
}: {
  token: string;
  boot: ReadyBoot;
}): React.ReactElement {
  const { engagement } = boot;
  // Reactive cards: cards are lifted into state (rather than a plain const
  // off `boot`) so a live-generation splice can grow the deck mid-session.
  // `orderDeckCards` is idempotent — `boot.cards` is already ordered (the
  // boot effect ran it once to compute `bootIndex`), so this just re-derives
  // the identical order as the initial state value.
  const [cards, setCards] = useState<CardModel[]>(() =>
    orderDeckCards(boot.cards, boot.responses),
  );
  const total = cards.length;

  const [ui, dispatch] = useReducer(deckReducer, undefined, () =>
    initialDeckState(boot.bootIndex, total),
  );
  const voiceEnabled = engagement.voice_enabled;
  const [responses, setResponses] = useState<Map<string, ClientResponse>>(
    () => new Map(boot.responses.map((r) => [r.card_id, r])),
  );
  const [fileUploads, setFileUploads] = useState<Map<string, UploadRow[]>>(
    () => {
      const m = new Map<string, UploadRow[]>();
      for (const u of boot.uploads) {
        if (u.kind !== "file") continue;
        m.set(u.card_id, [...(m.get(u.card_id) ?? []), u]);
      }
      return m;
    },
  );
  const [voiceUploads, setVoiceUploads] = useState<Map<string, UploadRow>>(
    () => {
      const m = new Map<string, UploadRow>();
      if (voiceEnabled) {
        for (const u of boot.uploads) {
          if (u.kind === "voice") m.set(u.card_id, u); // one per card
        }
      }
      return m;
    },
  );

  const pendingRef = useRef<PendingAction | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const indexRef = useRef(ui.index);
  indexRef.current = ui.index;
  // Reactive cards: read inside the async poll-completion callback, which
  // otherwise would've closed over a stale `cards` from whichever render
  // kicked the poll off.
  const cardsRef = useRef(cards);
  cardsRef.current = cards;
  // At most one BACKGROUND poll in flight at a time (mode stays "view", deck
  // already advanced) — the budget-expiry fallback path. A *new triggering*
  // save (via startFollowUpPoll) or unmount aborts whatever's running; a
  // save that doesn't itself trigger a generation leaves it alone.
  const activePollRef = useRef<AbortController | null>(null);
  // A DIFFERENT, second poll: the tight, in-place wait right after a
  // qualifying correction save (mode "waiting", deck has NOT advanced — see
  // awaitFollowUp). Cancelled by user-driven navigation (nav handlers below)
  // or unmount; activePollRef above only ever starts once this wait's
  // budget expires.
  const awaitPollRef = useRef<AbortController | null>(null);

  function clearRetry(): void {
    if (retryRef.current) {
      clearTimeout(retryRef.current);
      retryRef.current = null;
    }
    pendingRef.current = null;
  }

  function cancelActivePoll(): void {
    activePollRef.current?.abort();
    activePollRef.current = null;
  }

  function cancelAwaitPoll(): void {
    awaitPollRef.current?.abort();
    awaitPollRef.current = null;
  }

  // Clear any pending retry on unmount and whenever the card changes (mirrors
  // navigateTo() cancelling the retry timer in app.ts).
  useEffect(() => () => clearRetry(), []);
  useEffect(() => {
    clearRetry();
  }, [ui.index]);

  // Cancel any in-flight follow-up poll on unmount.
  useEffect(() => () => cancelActivePoll(), []);
  useEffect(() => () => cancelAwaitPoll(), []);

  const card = ui.index < total ? cards[ui.index] : null;
  const cardId = card?.id;

  // Mark a card viewed on first view (idempotent server-side; best-effort).
  useEffect(() => {
    if (!cardId || responses.has(cardId)) return;
    let active = true;
    clientApi
      .markViewed(token, cardId)
      .then((r) => {
        if (active) setResponses((m) => new Map(m).set(cardId, r));
      })
      .catch(() => {});
    return () => {
      active = false;
    };
    // Only re-run when the card changes; reading responses at run time is fine.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cardId]);

  // Reactive cards: poll `GET /api/generations` for the generation kicked
  // off by `responseId` (the just-saved correction on `parentCardId`), and
  // splice any resulting cards into the live deck. Guarded by
  // `activePollRef` so only the poll matching the currently-tracked
  // controller can act — a newer save (or unmount) aborts this one.
  async function pollForFollowUp(
    responseId: string,
    parentCardId: string,
    controller: AbortController,
  ): Promise<void> {
    const result = await runPoll(
      () => clientApi.generations(token, responseId),
      responseId,
      controller.signal,
    );
    if (activePollRef.current !== controller) return; // superseded or cancelled
    activePollRef.current = null;
    if (!result || result.status !== "completed" || result.cardIds.length === 0) {
      return;
    }

    let freshCards: CardModel[];
    try {
      freshCards = await clientApi.cards(token);
    } catch {
      return;
    }

    const liveCards = cardsRef.current;
    const existingIds = new Set(liveCards.map((c) => c.id));
    const newCards = result.cardIds
      .map((id) => freshCards.find((c) => c.id === id))
      .filter((c): c is CardModel => !!c && !existingIds.has(c.id));
    if (newCards.length === 0) return;

    // spliceIndexFor never returns an index at or before wherever the
    // respondent currently is; for the complete screen (index === total,
    // a sentinel, not a real card position) treat "current position" as
    // the last real card, so a parent-less generation lands at the very
    // end rather than one slot past it.
    const atComplete = indexRef.current >= liveCards.length;
    const effectiveIndex = atComplete
      ? Math.max(0, liveCards.length - 1)
      : indexRef.current;
    const insertAt = spliceIndexFor(liveCards, parentCardId, effectiveIndex);

    const updated = [...liveCards];
    updated.splice(insertAt, 0, ...newCards);
    setCards(updated);
    dispatch({ type: "cardsInserted", insertAt, count: newCards.length });
  }

  function startFollowUpPoll(responseId: string, parentCardId: string): void {
    cancelActivePoll();
    const controller = new AbortController();
    activePollRef.current = controller;
    void pollForFollowUp(responseId, parentCardId, controller);
  }

  // Reactive cards: refetch cards for a generation that resolved WHILE the
  // respondent was still parked in "waiting" mode on the corrected card
  // (cardIndex), and navigate straight into the first new follow-up.
  // Called only from awaitFollowUp below, and only once its own
  // awaitPollRef check has confirmed the wait wasn't cancelled out from
  // under it.
  async function pullInFollowUpAndNavigate(
    cardIds: string[],
    parentCardId: string,
    cardIndex: number,
  ): Promise<void> {
    let freshCards: CardModel[];
    try {
      freshCards = await clientApi.cards(token);
    } catch {
      if (indexRef.current === cardIndex) dispatch({ type: "advance" });
      return;
    }

    if (indexRef.current !== cardIndex) {
      // The respondent navigated away from the waiting card while this
      // fetch was in flight (the nav handlers below already cancelled the
      // wait) — don't yank them anywhere; fall back to the same
      // non-disruptive splice the background-poll fallback uses.
      const liveCards = cardsRef.current;
      const existingIds = new Set(liveCards.map((c) => c.id));
      const newCards = cardIds
        .map((id) => freshCards.find((c) => c.id === id))
        .filter((c): c is CardModel => !!c && !existingIds.has(c.id));
      if (newCards.length === 0) return;
      const atComplete = indexRef.current >= liveCards.length;
      const effectiveIndex = atComplete ? Math.max(0, liveCards.length - 1) : indexRef.current;
      const insertAt = spliceIndexFor(liveCards, parentCardId, effectiveIndex);
      const updated = [...liveCards];
      updated.splice(insertAt, 0, ...newCards);
      setCards(updated);
      dispatch({ type: "cardsInserted", insertAt, count: newCards.length });
      return;
    }

    const liveCards = cardsRef.current;
    const existingIds = new Set(liveCards.map((c) => c.id));
    const newCards = cardIds
      .map((id) => freshCards.find((c) => c.id === id))
      .filter((c): c is CardModel => !!c && !existingIds.has(c.id));

    if (newCards.length === 0) {
      dispatch({ type: "advance" });
      return;
    }

    const insertAt = spliceIndexFor(liveCards, parentCardId, cardIndex);
    const updated = [...liveCards];
    updated.splice(insertAt, 0, ...newCards);
    setCards(updated);
    dispatch({ type: "followUpReady", insertAt, count: newCards.length });
  }

  // Reactive cards: the in-place wait after a qualifying correction save.
  // Stays on `cardIndex` (mode "waiting") instead of advancing, polling
  // `waitSchedule` (~8s) for the generation kicked off by this save. Owns
  // the eventual advance()/navigate for this save:
  //   - completed w/ cards inside the budget -> splice + navigate into the
  //     first follow-up (pullInFollowUpAndNavigate).
  //   - skipped/failed -> dispatch "advance", no follow-up.
  //   - budget expires (result === null) -> dispatch "advance", then fall
  //     back to the existing background poll (startFollowUpPoll) on the
  //     longer pollSchedule, so a late completion still splices in behind
  //     the respondent instead of being lost.
  // A fresh call (a new trigger) supersedes both this and any earlier
  // background poll; the nav handlers cancel this if the respondent moves
  // off the waiting card before it resolves.
  async function awaitFollowUp(
    responseId: string,
    parentCardId: string,
    cardIndex: number,
  ): Promise<void> {
    cancelActivePoll();
    cancelAwaitPoll();
    const controller = new AbortController();
    awaitPollRef.current = controller;
    dispatch({ type: "awaitFollowUp" });

    const result = await runPoll(
      () => clientApi.generations(token, responseId),
      responseId,
      controller.signal,
      waitSchedule,
    );
    if (awaitPollRef.current !== controller) return; // cancelled — navigated away
    awaitPollRef.current = null;

    if (result?.status === "completed" && result.cardIds.length > 0) {
      await pullInFollowUpAndNavigate(result.cardIds, parentCardId, cardIndex);
      return;
    }
    if (result?.status === "skipped" || result?.status === "failed") {
      dispatch({ type: "advance" });
      return;
    }

    // Budget expired with no terminal status yet.
    dispatch({ type: "advance" });
    startFollowUpPoll(responseId, parentCardId);
  }

  async function performSave(action: PendingAction): Promise<void> {
    const startIndex = indexRef.current;
    const current = startIndex < total ? cards[startIndex] : null;
    if (!current) return;

    clearRetry();
    // Deliberately not cancelling an in-flight BACKGROUND follow-up poll
    // here: this save might just be the respondent continuing to answer
    // while an earlier correction's generation is still running in the
    // background, and cancelling it would drop that follow-up for good. If
    // *this* save turns out to be a new trigger, awaitFollowUp() below
    // cancels the old background poll itself before starting its own wait,
    // so "at most one poll of each kind" still holds. (The in-place
    // "waiting" poll can't already be running here — mode "waiting" renders
    // no actions, so no new save can be initiated from the current card
    // while one is in flight.)
    pendingRef.current = action;
    dispatch({ type: "saveStart" });

    const fileIds = (fileUploads.get(current.id) ?? []).map((u) => u.id);
    const hasVoice = voiceUploads.has(current.id);
    const { state, response_value } = encodeResponse(action, fileIds, hasVoice);

    let saved: ClientResponse;
    try {
      saved = await clientApi.saveResponse(token, {
        card_id: current.id,
        state,
        response_value,
      });
    } catch {
      dispatch({
        type: "saveFailed",
        message: "Could not save just now. We will retry automatically.",
      });
      retryRef.current = setTimeout(() => {
        retryRef.current = null;
        const again = pendingRef.current;
        if (again) void performSave(again);
      }, 10_000);
      return;
    }

    clearRetry();
    setResponses((m) => new Map(m).set(current.id, saved));
    clientApi.heartbeat(token).catch(() => {});

    // The "Send note" path gets an explicit confirmation — the deck advances
    // right after, so the toast is the signal the note actually went through.
    if (action.kind === "note") toast.success("Note sent");

    // Only act on this save if the recipient is still on the card we saved
    // (mirrors app.ts's cardIndex capture).
    if (indexRef.current !== startIndex) return;

    // Reactive cards: a "Needs edit" correction may have kicked off a
    // generation. Rather than advancing immediately, wait in place a short
    // moment for it — most real generations resolve in ~3.5-4s, well inside
    // awaitFollowUp's budget, so a same-turn follow-up usually becomes the
    // very next screen instead of quietly appearing behind the respondent
    // later. A false-positive client-side guess just costs one wasted wait
    // (the backend re-derives the trigger independently).
    if (
      engagement.reactive_cards_enabled &&
      hasTriggerPotential(current.response_type, state, response_value)
    ) {
      void awaitFollowUp(saved.id, current.id, startIndex);
      return;
    }

    dispatch({ type: "advance" });
  }

  const handlers: DeckHandlers = {
    onConfirm: () => void performSave({ kind: "confirm" }),
    onEditStart: () => dispatch({ type: "editStart" }),
    onEditCancel: () => dispatch({ type: "editCancel" }),
    onEditSubmit: (correction) => void performSave({ kind: "edit", correction }),
    onSingleSelect: (option, note) =>
      void performSave({ kind: "single-select", option, note }),
    onNoteSubmit: (note, option) =>
      void performSave({ kind: "note", note, option }),
    onMultiSelectSubmit: (options, note) =>
      void performSave({ kind: "multi-select", options, note }),
    onTextSubmit: (text, note) =>
      void performSave({ kind: "text", text, note }),
    onLinkSubmit: (url, note) => void performSave({ kind: "link", url, note }),
    onContactSubmit: (contact, note) =>
      void performSave({ kind: "contact", ...contact, note }),
    onFilesContinue: (note) =>
      void performSave({ kind: "files-continue", note }),
    onSkip: (note) => void performSave({ kind: "skip", note }),
    onRetry: () => {
      const p = pendingRef.current;
      if (p) void performSave(p);
    },
    // Reactive cards: any user-driven navigation away from a "waiting" card
    // cancels that wait outright and honors the navigation (mirrors
    // navigateTo() in app.ts) — awaitFollowUp's poll callback checks
    // awaitPollRef before acting, so a cancelled-then-resolved poll is a
    // no-op once it lands.
    onNavBack: () => {
      cancelAwaitPoll();
      dispatch({ type: "navigate", index: indexRef.current - 1 });
    },
    onNavForward: () => {
      cancelAwaitPoll();
      dispatch({ type: "navigate", index: indexRef.current + 1 });
    },
    onNavJumpTo: (index) => {
      cancelAwaitPoll();
      dispatch({ type: "navigate", index });
    },
    onPickerOpen: () => dispatch({ type: "openPicker" }),
    onPickerClose: () => dispatch({ type: "closePicker" }),
    onAttachmentOpen: () => dispatch({ type: "openModal" }),
    onAttachmentClose: () => dispatch({ type: "closeModal" }),
  };

  if (!card) {
    // Answers stay editable after completion — the review hint drops the
    // recipient back on the last card.
    return (
      <CompleteCard
        name={engagement.recipient_name}
        onReview={
          total > 0
            ? () => dispatch({ type: "navigate", index: total - 1 })
            : undefined
        }
      />
    );
  }

  const media = {
    token,
    voiceEnabled,
    cardFiles: fileUploads.get(card.id) ?? [],
    voiceUpload: voiceUploads.get(card.id),
    onFileUploaded: (row: UploadRow) =>
      setFileUploads((m) => {
        const n = new Map(m);
        n.set(row.card_id, [...(n.get(row.card_id) ?? []), row]);
        return n;
      }),
    onFileRemoved: (uploadId: string) =>
      setFileUploads((m) => {
        const n = new Map(m);
        n.set(
          card.id,
          (n.get(card.id) ?? []).filter((u) => u.id !== uploadId),
        );
        return n;
      }),
    onVoiceSaved: (row: UploadRow) =>
      setVoiceUploads((m) => new Map(m).set(card.id, row)),
    onVoiceDeleted: () =>
      setVoiceUploads((m) => {
        const n = new Map(m);
        n.delete(card.id);
        return n;
      }),
  };

  return (
    <>
      <CardView
        card={card}
        position={ui.index + 1}
        total={total}
        mode={ui.mode}
        saveError={ui.saveError}
        showResume={ui.showResume}
        existing={responses.get(card.id)}
        orgLogoSrc={boot.orgLogoSrc}
        orgName={engagement.name}
        handlers={handlers}
        media={media}
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
