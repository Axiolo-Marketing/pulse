import {
  clientApi,
  ApiError,
  type Card,
  type Engagement,
  type ClientResponse,
  type ResponseState,
  type UploadRow,
} from "../lib/api";
import { applyBranding } from "../lib/branding";
import {
  hasTriggerPotential,
  orderDeckCards,
  runPoll,
  spliceIndexFor,
  waitSchedule,
} from "../lib/deck-order";
import { Recorder, RecorderError, extForMime } from "../lib/recorder";
import {
  renderCard,
  renderComplete,
  renderError,
  renderLoading,
  showToast,
  type CardHandlers,
  type CardMode,
  type CompletedUpload,
  type PendingUpload,
  type VoiceState,
} from "../lib/render";

interface BootData {
  client: Engagement;
  cards: Card[];
  responses: Map<string, ClientResponse>;
  uploads: Map<string, CompletedUpload[]>; // keyed by card_id
  voiceUploads: Map<string, UploadRow>; // keyed by card_id (one voice note per card)
}

const BASE_URL = (import.meta.env.BASE_URL ?? "/") as string;
const MAX_FILE_BYTES = 25 * 1024 * 1024;
const MAX_FILES_PER_CARD = 5;

async function main(): Promise<void> {
  const mount = document.getElementById("app");
  if (!mount) return;

  renderLoading(mount);

  const params = new URLSearchParams(window.location.search);
  const token = params.get("t");

  if (!token) {
    renderError(
      mount,
      "This link is missing a code",
      "Please check the link your consultant sent you."
    );
    return;
  }

  const boot = await loadBootData(token);
  if (!boot) {
    renderError(
      mount,
      "We could not find your engagement",
      "Please check the link or contact Tom."
    );
    return;
  }

  // Theme the deck to the operator org before the first paint so the
  // client never sees the default Axiolo blue flash to the brand colour.
  applyBranding(boot.client.org_branding);

  // Resolve the org logo once (the bytes need the token in a header, so
  // we fetch a Blob → object URL rather than a plain <img src>). One URL
  // for the whole session — reused on every draw.
  const orgLogoSrc = boot.client.org_logo_path
    ? await clientApi.logoObjectUrl(token)
    : null;

  runApp({ mount, token, orgLogoSrc, ...boot });
}

async function loadBootData(token: string): Promise<BootData | null> {
  let client: Engagement;
  try {
    client = await clientApi.me(token);
  } catch (err) {
    if (err instanceof ApiError) return null;
    throw err;
  }

  const [rawCards, responsesList, uploadsList] = await Promise.all([
    clientApi.cards(token),
    clientApi.responses(token),
    clientApi.uploads(token),
  ]);

  // Reactive cards: an AI-generated follow-up is appended at the end of the
  // deck server-side (order_index = max+1). Re-order for display so it shows
  // up directly after the card whose correction triggered it, even on a
  // fresh page load — see src/lib/deck-order.ts.
  const cards = orderDeckCards(rawCards, responsesList);

  const responses = new Map<string, ClientResponse>(
    responsesList.map((r) => [r.card_id, r])
  );

  const uploads = new Map<string, CompletedUpload[]>();
  const voiceUploads = new Map<string, UploadRow>();
  for (const row of uploadsList) {
    if (row.kind === "voice") {
      // Voice answers are only surfaced when the engagement has voice
      // enabled. If a recording predates the toggle being turned off, we
      // simply don't seed it — the deck shows no voice UI, and the
      // recording stays safely in the DB (still visible in /admin/).
      if (client.voice_enabled) {
        // One voice note per card — last one wins if somehow duplicated.
        voiceUploads.set(row.card_id, row);
      }
      continue;
    }
    const list = uploads.get(row.card_id) ?? [];
    list.push({
      id: row.id,
      name: row.file_name,
      sizeBytes: row.file_size_bytes,
    });
    uploads.set(row.card_id, list);
  }

  return { client, cards, responses, uploads, voiceUploads };
}

interface RunCtx extends BootData {
  mount: HTMLElement;
  token: string;
  /** Object URL for the operator org's logo, resolved once on boot, or
   * `null` when the org has no logo. Passed to every `renderCard` so the
   * top-bar shows the org mark instead of the Axiolo wordmark. */
  orgLogoSrc: string | null;
}

function runApp(ctx: RunCtx): void {
  const { mount, token, client, cards, responses, uploads, voiceUploads, orgLogoSrc } =
    ctx;

  const bootIndex = firstUnansweredIndex(cards, responses);
  let index = bootIndex;
  let mode: CardMode = "view";
  let saveError: string | undefined;
  let modalOpen = false;
  let pickerOpen = false;
  let pending: PendingAction | undefined;

  // Per-card UI scratch state. Reset whenever the card changes.
  let draftSelections: Set<string> = new Set();
  let pendingUploads: PendingUpload[] = [];

  // ── Voice recorder state ────────────────────────────────────────────────
  // One active recorder at a time. The recorder, its tick timer, and the
  // in-progress object URL are scratch for the current card only; navigating
  // away discards any in-progress take (see navigateTo/advance).
  const recorder = new Recorder();
  // Object URLs we create for inline playback. Tracked so we can revoke
  // them when a card's recording is replaced or deleted.
  const voiceAudioUrls = new Map<string, string>(); // card_id → object URL
  let voiceError: string | undefined;
  let voiceTimer: ReturnType<typeof setInterval> | undefined;
  let voiceStartedAt = 0; // epoch ms when the current recording (re)started
  let voiceAccumulatedMs = 0; // elapsed before the latest pause
  let voiceUploading = false;
  // Serializes the async delete-then-(re)record / delete paths so a
  // double-tap can't fire two DELETEs for the same upload (the second
  // would 404 and surface a spurious error over a successful first).
  let voiceMutating = false;

  // Revoke any cached voice playback object URLs when the deck tears down,
  // so a long session with many recordings/re-records doesn't leak blobs.
  if (typeof window !== "undefined") {
    window.addEventListener("pagehide", () => {
      for (const url of voiceAudioUrls.values()) URL.revokeObjectURL(url);
      voiceAudioUrls.clear();
    });
  }

  // ── Reactive cards: follow-up poll state ────────────────────────────────
  // At most one BACKGROUND poll in flight at a time (mode stays "view",
  // deck already advanced) — this is the budget-expiry fallback path.
  // Started only from startFollowUpPoll below; cancelled when a *new*
  // triggering save supersedes it or the page unloads. A non-triggering
  // save does NOT cancel it — see performSave.
  let pollController: AbortController | undefined;
  let pollActive = false; // drives the "Checking..." hint under the progress area

  const cancelPoll = (): void => {
    pollController?.abort();
    pollController = undefined;
    pollActive = false;
  };

  if (typeof window !== "undefined") {
    window.addEventListener("pagehide", cancelPoll);
  }

  // ── Reactive cards: in-place "waiting" state ────────────────────────────
  // A DIFFERENT, second poll: the tight, in-place wait right after a
  // qualifying correction save (mode = "waiting", deck has NOT advanced —
  // see beginAwaitFollowUp). Distinct from pollController above, which only
  // ever starts once this wait's budget expires. Cancelled by navigateTo
  // (the respondent moving off the waiting card honors their navigation and
  // drops the wait) or the page unloading.
  let awaitController: AbortController | undefined;

  const cancelAwait = (): void => {
    awaitController?.abort();
    awaitController = undefined;
  };

  if (typeof window !== "undefined") {
    window.addEventListener("pagehide", cancelAwait);
  }

  const clearVoiceTimer = (): void => {
    if (voiceTimer) {
      clearInterval(voiceTimer);
      voiceTimer = undefined;
    }
  };

  const voiceElapsedSeconds = (): number => {
    const live =
      recorder.state === "recording" ? Date.now() - voiceStartedAt : 0;
    return Math.floor((voiceAccumulatedMs + live) / 1000);
  };

  // Build the VoiceState the renderer consumes for the current card.
  const voiceStateFor = (cardId: string): VoiceState => {
    if (recorder.state === "recording") {
      return { phase: "recording", elapsedSeconds: voiceElapsedSeconds(), error: voiceError };
    }
    if (recorder.state === "paused") {
      return { phase: "paused", elapsedSeconds: voiceElapsedSeconds(), error: voiceError };
    }
    if (voiceUploading) {
      return { phase: "uploading", elapsedSeconds: 0, error: voiceError };
    }
    const existing = voiceUploads.get(cardId);
    if (existing) {
      return {
        phase: "done",
        elapsedSeconds: 0,
        audioUrl: voiceAudioUrls.get(cardId),
        error: voiceError,
      };
    }
    return { phase: "idle", elapsedSeconds: 0, error: voiceError };
  };

  // Reset every per-card voice scratch when the card changes. Discards an
  // in-progress take (only one recorder at a time, per the deck contract).
  const resetVoiceForCardChange = (): void => {
    if (recorder.state !== "idle") recorder.cancel();
    clearVoiceTimer();
    voiceError = undefined;
    voiceStartedAt = 0;
    voiceAccumulatedMs = 0;
    voiceUploading = false;
  };

  // When navigating back to a card the user already answered, prime the
  // selection state so multi-select chips and single-select highlights show
  // their prior choices. Text/link/contact inputs are pre-filled at render
  // time from the response_value directly.
  const seedDraftFromResponse = (card: Card): void => {
    draftSelections = new Set();
    const r = responses.get(card.id);
    if (!r || r.state !== "answered") return;
    const v = (r.response_value ?? {}) as { selected?: string | string[] };
    if (card.response_type === "multi-select" && Array.isArray(v.selected)) {
      draftSelections = new Set(v.selected);
    } else if (
      card.response_type === "single-select" &&
      typeof v.selected === "string"
    ) {
      draftSelections = new Set([v.selected]);
    }
  };
  if (index < cards.length) seedDraftFromResponse(cards[index]);

  // Resume banner shown once on boot when the user is returning past the
  // start of the deck. It dismisses on the first save/advance — simpler
  // and friendlier than a time-based fade.
  let showResume = bootIndex > 0 && bootIndex < cards.length;

  // Auto-retry timer for failed saves. Per spec, retry every 10 seconds
  // until success. Cleared on success, manual retry, or when the user
  // navigates to a new card.
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  const clearRetryTimer = (): void => {
    if (retryTimer) {
      clearTimeout(retryTimer);
      retryTimer = undefined;
    }
  };

  type PendingAction =
    | { kind: "confirm" }
    | { kind: "edit"; correction: string }
    | { kind: "skip"; note?: string }
    | { kind: "single-select"; option: string; note?: string }
    // The note as the answer itself (single-select's "Send note" button);
    // `option` preserves the currently-highlighted choice, if any.
    | { kind: "note"; note: string; option?: string }
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

  // markViewed inserts a viewed row for this card if no row exists yet.
  // The backend's POST /api/responses/view is idempotent via the
  // (card_id, engagement_id) unique constraint — safe to fire on every render.
  const markViewed = (cardId: string): void => {
    if (responses.has(cardId)) return;
    clientApi
      .markViewed(token, cardId)
      .then((data) => responses.set(cardId, data))
      .catch((err) => console.warn("mark viewed failed:", err));
  };

  const draw = (): void => {
    if (index >= cards.length) {
      // Greet the recipient by their own name when we have it (multi-
      // respondent: each recipient is named), falling back to the client
      // (company) name so the message still reads naturally. The review
      // hint drops the recipient back on the last card — answers stay
      // editable after completion, this is just the way back in.
      renderComplete(
        mount,
        client.recipient_name?.trim() || client.name,
        cards.length > 0 ? () => navigateTo(cards.length - 1) : undefined
      );
      return;
    }
    const card = cards[index];
    if (mode === "view") markViewed(card.id);
    // A previously saved voice note (boot or back-nav) needs its playback
    // bytes fetched once. Resolve lazily, then re-draw so the <audio> gets
    // its src. No-ops once cached or when there's nothing to play.
    if (
      recorder.state === "idle" &&
      !voiceUploading &&
      voiceUploads.has(card.id) &&
      !voiceAudioUrls.has(card.id)
    ) {
      void ensureVoicePlaybackUrl(card.id).then((url) => {
        if (url && index < cards.length && cards[index].id === card.id) draw();
      });
    }
    renderCard(mount, {
      card,
      position: index + 1,
      total: cards.length,
      mode,
      saveError,
      baseUrl: BASE_URL,
      uploads: uploads.get(card.id) ?? [],
      pending: pendingUploads,
      voice: voiceStateFor(card.id),
      voiceEnabled: client.voice_enabled,
      modalOpen,
      pickerOpen,
      draftSelections,
      showResume,
      existingResponse: responses.get(card.id),
      cards,
      responses,
      handlers,
      // Reactive cards: a quiet one-liner while we're checking whether the
      // last correction needs a follow-up. Never an alert/confirm — just an
      // inline status line under the progress area (see renderCard).
      pollHint: pollActive
        ? "Checking if we need a quick follow-up…"
        : undefined,
      // Operator-org branding on the client deck: the logo object URL was
      // resolved once on boot (token in a header, not the URL). A null
      // logo keeps the Axiolo wordmark. `orgName` is left unset — the
      // engagement payload carries no operator-org name, so `renderCard`
      // falls back to a generic "Organization" alt.
      orgLogoSrc,
    });
  };

  const navigateTo = (newIndex: number): void => {
    if (newIndex < 0 || newIndex > cards.length) return;
    if (newIndex === index && !pickerOpen) return;
    clearRetryTimer();
    resetVoiceForCardChange();
    // Reactive cards: any user-driven navigation (nav-back/forward, picker
    // jump) away from a "waiting" card cancels that wait outright and
    // honors the navigation — see beginAwaitFollowUp's poll callback, which
    // checks awaitController before acting.
    cancelAwait();
    index = newIndex;
    mode = "view";
    saveError = undefined;
    pending = undefined;
    modalOpen = false;
    pickerOpen = false;
    pendingUploads = [];
    showResume = false;
    if (index < cards.length) seedDraftFromResponse(cards[index]);
    draw();
  };

  const advance = (): void => {
    clearRetryTimer();
    resetVoiceForCardChange();
    index += 1;
    mode = "view";
    saveError = undefined;
    pending = undefined;
    modalOpen = false;
    pickerOpen = false;
    pendingUploads = [];
    showResume = false;
    if (index < cards.length) seedDraftFromResponse(cards[index]);
    draw();
  };

  // Reactive cards: patch the header chrome (position/total, forward-arrow
  // enablement) and the poll hint in place, without going through draw()'s
  // full mount.innerHTML rebuild. draw() re-renders the current card from
  // scratch every time, which would silently wipe out anything the
  // respondent is mid-typing (short/long-text, link, contact fields, the
  // free-form note) — those live only in the DOM until submit, not in any
  // module state we could reseed from. A poll resolving in the background
  // must never do that, so any path that doesn't actually change which card
  // is displayed goes through here instead of draw().
  const updateProgressChrome = (): void => {
    if (index >= cards.length) return; // complete screen has no chrome to patch

    const progressBtn = mount.querySelector<HTMLButtonElement>(".progress-btn");
    if (progressBtn) {
      progressBtn.innerHTML = `
        ${index + 1} of ${cards.length}
        <span class="progress-caret" aria-hidden="true">▾</span>
      `;
    }
    const forwardBtn = mount.querySelector<HTMLButtonElement>(
      '[data-action="nav-forward"]'
    );
    if (forwardBtn) forwardBtn.disabled = index + 1 >= cards.length;

    let hintEl = mount.querySelector<HTMLElement>(".poll-hint");
    if (pollActive) {
      if (!hintEl) {
        hintEl = document.createElement("div");
        hintEl.className = "poll-hint";
        hintEl.setAttribute("role", "status");
        mount.querySelector("header.topbar")?.insertAdjacentElement(
          "afterend",
          hintEl
        );
      }
      if (hintEl) hintEl.textContent = "Checking if we need a quick follow-up…";
    } else if (hintEl) {
      hintEl.remove();
    }
  };

  // Reactive cards: refetch cards, pick out the ones the generation actually
  // created (by id), and splice them into the live deck array. If the
  // respondent already reached the complete screen while the generation was
  // running, jump them straight to the new follow-up instead of leaving them
  // stranded on "All done". Otherwise the respondent's own position is left
  // untouched (spliceIndexFor never inserts at or before it) — only the
  // header chrome needs to reflect the new card count, via
  // updateProgressChrome() rather than a full draw().
  const pullInNewCards = async (
    cardIds: string[],
    parentCardId: string
  ): Promise<void> => {
    let freshCards: Card[];
    try {
      freshCards = await clientApi.cards(token);
    } catch (err) {
      console.warn("Refetch cards after generation failed:", err);
      return;
    }

    const existingIds = new Set(cards.map((c) => c.id));
    const newCards = cardIds
      .map((id) => freshCards.find((c) => c.id === id))
      .filter((c): c is Card => !!c && !existingIds.has(c.id));
    if (newCards.length === 0) return;

    // spliceIndexFor never returns an index at or before wherever the
    // respondent currently is; for the complete screen (index === length,
    // a sentinel, not a real card position) treat "current position" as the
    // last real card, so a parent-less generation still lands at the very
    // end rather than one slot past it.
    const atComplete = index >= cards.length;
    const effectiveIndex = atComplete ? Math.max(0, cards.length - 1) : index;
    const insertAt = spliceIndexFor(cards, parentCardId, effectiveIndex);
    cards.splice(insertAt, 0, ...newCards);

    if (atComplete) {
      clearRetryTimer();
      resetVoiceForCardChange();
      index = insertAt;
      mode = "view";
      saveError = undefined;
      pending = undefined;
      modalOpen = false;
      pickerOpen = false;
      pendingUploads = [];
      showResume = false;
      seedDraftFromResponse(cards[index]);
      draw();
      return;
    }
    updateProgressChrome();
  };

  // Start (or restart) polling for a generation kicked off by the response
  // just saved for `parentCardId`. Only one poll runs at a time — a fresh
  // call (or cancelPoll()) supersedes whatever's in flight.
  const startFollowUpPoll = (responseId: string, parentCardId: string): void => {
    cancelPoll();
    const controller = new AbortController();
    pollController = controller;
    pollActive = true;
    draw();

    void runPoll(
      () => clientApi.generations(token, responseId),
      responseId,
      controller.signal
    ).then((result) => {
      if (pollController !== controller) return; // superseded or cancelled
      pollController = undefined;
      pollActive = false;

      if (
        !result ||
        result.status !== "completed" ||
        result.cardIds.length === 0
      ) {
        // Clear the hint without a full draw() — the respondent may be
        // mid-typing on whatever card they've since moved to, and a
        // draw() here would blow that away for no reason (nothing about
        // the deck actually changed on this path).
        updateProgressChrome();
        return;
      }
      void pullInNewCards(result.cardIds, parentCardId);
    });
  };

  // Reactive cards: refetch cards for a generation that resolved WHILE the
  // respondent was still parked in "waiting" mode on the corrected card
  // (cardIndex), and navigate straight into the first new follow-up — it
  // becomes the very next screen, per-card UI state reset exactly like any
  // other navigateTo(). Called only from beginAwaitFollowUp below, and only
  // once its own awaitController check has confirmed the wait wasn't
  // cancelled out from under it.
  const pullInFollowUpAndNavigate = async (
    cardIds: string[],
    parentCardId: string,
    cardIndex: number
  ): Promise<void> => {
    let freshCards: Card[];
    try {
      freshCards = await clientApi.cards(token);
    } catch (err) {
      console.warn("Refetch cards after generation failed:", err);
      if (mode === "waiting" && index === cardIndex) advance();
      return;
    }

    if (mode !== "waiting" || index !== cardIndex) {
      // The respondent navigated away from the waiting card while this
      // fetch was in flight (navigateTo already cancelled the wait) — don't
      // yank them anywhere; fall back to the same non-disruptive splice the
      // background-poll fallback uses.
      void pullInNewCards(cardIds, parentCardId);
      return;
    }

    const existingIds = new Set(cards.map((c) => c.id));
    const newCards = cardIds
      .map((id) => freshCards.find((c) => c.id === id))
      .filter((c): c is Card => !!c && !existingIds.has(c.id));

    if (newCards.length === 0) {
      advance();
      return;
    }

    const insertAt = spliceIndexFor(cards, parentCardId, cardIndex);
    cards.splice(insertAt, 0, ...newCards);
    navigateTo(insertAt);
  };

  // Reactive cards: the in-place wait after a qualifying correction save.
  // Stays on `cardIndex` (mode = "waiting", see render.ts) instead of
  // advancing, polling `waitSchedule` (~8s) for the generation kicked off
  // by this save. Owns the eventual advance()/navigateTo() for this save:
  //   - completed w/ cards inside the budget -> splice + navigate into the
  //     first follow-up (pullInFollowUpAndNavigate).
  //   - skipped/failed -> advance() normally, no follow-up.
  //   - budget expires (result === null) -> advance() normally, then fall
  //     back to the existing background poll (startFollowUpPoll) on the
  //     longer pollSchedule, so a late completion still splices in behind
  //     the respondent instead of being lost.
  // A fresh call (a new trigger) supersedes both this and any earlier
  // background poll; navigateTo cancels this if the respondent moves off
  // the waiting card before it resolves.
  const beginAwaitFollowUp = (
    responseId: string,
    parentCardId: string,
    cardIndex: number
  ): void => {
    cancelPoll();
    cancelAwait();
    const controller = new AbortController();
    awaitController = controller;
    mode = "waiting";
    saveError = undefined;
    pending = undefined;
    draw();

    void runPoll(
      () => clientApi.generations(token, responseId),
      responseId,
      controller.signal,
      waitSchedule
    ).then((result) => {
      if (awaitController !== controller) return; // cancelled — navigated away
      awaitController = undefined;

      if (result?.status === "completed" && result.cardIds.length > 0) {
        void pullInFollowUpAndNavigate(result.cardIds, parentCardId, cardIndex);
        return;
      }
      if (result?.status === "skipped" || result?.status === "failed") {
        advance();
        return;
      }

      // Budget expired with no terminal status yet.
      advance();
      startFollowUpPoll(responseId, parentCardId);
    });
  };

  const performSave = async (action: PendingAction): Promise<void> => {
    clearRetryTimer();
    // Deliberately not cancelling an in-flight BACKGROUND follow-up poll
    // here: this save might just be the respondent continuing to answer
    // while an earlier correction's generation is still running in the
    // background, and cancelling it would drop that follow-up for good (it
    // only ever gets spliced in via that poll — a later reload is the only
    // other way it'd surface). If *this* save turns out to be a new
    // trigger, beginAwaitFollowUp() below cancels the old background poll
    // itself before starting its own wait, so "at most one poll of each
    // kind" still holds. (The in-place "waiting" poll can't already be
    // running here — mode === "waiting" renders no actions, so no new save
    // can be initiated from the current card while one is in flight.)
    pending = action;
    saveError = undefined;
    mode = "saving";
    draw();

    const cardIndex = index;
    const card = cards[cardIndex];

    let state: ResponseState;
    let value: unknown;

    // withNote folds an optional free-form note into the structured value.
    // null is preserved (skip with no note); objects get a note field.
    const withNote = (v: unknown, note?: string): unknown => {
      if (!note) return v;
      if (v === null) return { note };
      if (typeof v === "object" && v !== null) return { ...v, note };
      return v;
    };

    // The backend derives `engagement_id` from the request's token and sets
    // `answered_at`/`viewed_at` server-side based on `state`, so we only
    // ship `{card_id, state, response_value}`.
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
      case "note":
        state = "answered";
        value = action.option
          ? { selected: action.option, note: action.note }
          : { note: action.note };
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
          action.note
        );
        break;
      case "files-continue": {
        const list = uploads.get(card.id) ?? [];
        const hasFiles = list.length > 0;
        const hasNote = !!action.note;
        state = hasFiles || hasNote ? "answered" : "skipped";
        const base = hasFiles ? { file_ids: list.map((u) => u.id) } : null;
        value = withNote(base, action.note);
        break;
      }
    }

    // A voice recording supplements the normal answer: a voice-only answer
    // (no typed value / no selection) still counts as answered. We never
    // touch `response_value` — the voice note lives as a separate upload.
    if (voiceUploads.has(card.id) && state !== "answered") {
      state = "answered";
    }

    let saved: ClientResponse;
    try {
      saved = await clientApi.saveResponse(token, {
        card_id: card.id,
        state,
        response_value: value,
      });
    } catch (err) {
      console.error("Save failed:", err);
      saveError = "Could not save just now. We will retry automatically.";
      mode = "view";
      clearRetryTimer();
      retryTimer = setTimeout(() => {
        retryTimer = undefined;
        if (pending) void performSave(pending);
      }, 10_000);
      draw();
      return;
    }

    clearRetryTimer();
    responses.set(card.id, saved);

    // The "Send note" path gets an explicit confirmation — the deck advances
    // right after, so the toast is the signal the note actually went through.
    if (action.kind === "note") showToast("Note sent");

    // Best-effort heartbeat — errors are non-fatal.
    clientApi
      .heartbeat(token)
      .catch((err) => console.warn("last_active_at touch failed:", err));

    // Only act on this save if the respondent is still on the card we saved
    // — they may have navigated away (nav-back/forward, picker jump) while
    // the save round-tripped. navigateTo() already reset mode/index for
    // wherever they went; piling a "waiting" state or an advance() on top of
    // that here would either strand them on the wrong card or skip one.
    if (index !== cardIndex) return;

    // Reactive cards: a "Needs edit" correction may have kicked off a
    // generation. Rather than advancing immediately, wait in place a short
    // moment for it — most real generations resolve in ~3.5-4s, well inside
    // beginAwaitFollowUp's budget, so a same-turn follow-up usually becomes
    // the very next screen instead of quietly appearing behind the
    // respondent later. A false-positive client-side guess just costs one
    // wasted wait (the backend re-derives the trigger independently).
    if (
      client.reactive_cards_enabled &&
      hasTriggerPotential(card.response_type, state, value)
    ) {
      beginAwaitFollowUp(saved.id, card.id, cardIndex);
      return;
    }

    advance();
  };

  const handleFiles = async (files: FileList): Promise<void> => {
    const card = cards[index];
    const existing = (uploads.get(card.id) ?? []).length;
    const inflight = pendingUploads.filter((p) => !p.error).length;
    const room = MAX_FILES_PER_CARD - existing - inflight;
    const toUpload = Array.from(files).slice(0, Math.max(0, room));

    for (const file of toUpload) {
      const tempId = crypto.randomUUID();
      if (file.size > MAX_FILE_BYTES) {
        pendingUploads = [
          ...pendingUploads,
          {
            tempId,
            name: file.name,
            sizeBytes: file.size,
            progress: 0,
            error: "Too large (max 25MB)",
          },
        ];
        draw();
        continue;
      }

      pendingUploads = [
        ...pendingUploads,
        { tempId, name: file.name, sizeBytes: file.size, progress: 0 },
      ];
      draw();

      // Single multipart POST replaces the old storage.upload + row.insert
      // pair — the backend handles disk write + row insert atomically.
      let row;
      try {
        row = await clientApi.upload(token, card.id, file);
      } catch (err) {
        console.error("Upload failed:", err);
        const detail =
          err instanceof ApiError ? err.detail : "Upload failed";
        pendingUploads = pendingUploads.map((p) =>
          p.tempId === tempId ? { ...p, error: detail } : p
        );
        draw();
        continue;
      }

      const cardUploads = uploads.get(card.id) ?? [];
      cardUploads.push({
        id: row.id,
        name: row.file_name,
        sizeBytes: row.file_size_bytes,
      });
      uploads.set(card.id, cardUploads);
      pendingUploads = pendingUploads.filter((p) => p.tempId !== tempId);
      draw();
    }
  };

  const removeUpload = async (uploadId: string): Promise<void> => {
    const card = cards[index];
    const list = uploads.get(card.id) ?? [];
    const target = list.find((u) => u.id === uploadId);
    if (!target) return;

    try {
      // Backend removes both the DB row and the on-disk file in one call.
      await clientApi.deleteUpload(token, uploadId);
    } catch (err) {
      console.error("Upload delete failed:", err);
      return;
    }

    uploads.set(
      card.id,
      list.filter((u) => u.id !== uploadId)
    );
    draw();
  };

  // ── Voice recorder handlers ─────────────────────────────────────────────

  // Revoke and forget a card's cached playback object URL.
  const revokeVoiceUrl = (cardId: string): void => {
    const url = voiceAudioUrls.get(cardId);
    if (url) {
      URL.revokeObjectURL(url);
      voiceAudioUrls.delete(cardId);
    }
  };

  // Fetch an existing voice upload's bytes (token rides in a header, so we
  // can't use a plain <audio src>) and cache an object URL for inline
  // playback. Returns the URL, or null on failure. No-ops if already cached.
  const ensureVoicePlaybackUrl = async (cardId: string): Promise<string | null> => {
    const cached = voiceAudioUrls.get(cardId);
    if (cached) return cached;
    const row = voiceUploads.get(cardId);
    if (!row) return null;
    const url = await clientApi.fileObjectUrl(token, row.id);
    if (url) voiceAudioUrls.set(cardId, url);
    return url;
  };

  // Start (or restart, for "Re-record") a recording on the current card.
  const startVoiceRecording = async (): Promise<void> => {
    // Defence: the record button isn't rendered when voice is disabled,
    // so this is unreachable — but a disabled engagement must never start
    // a recording even if some path tried to.
    if (!client.voice_enabled) return;
    if (voiceMutating || recorder.state !== "idle") return;
    voiceMutating = true;
    const card = cards[index];
    voiceError = undefined;

    // Re-record: drop the previously saved take first so the card returns
    // to a clean recording state. Delete server-side, then clear local.
    const existing = voiceUploads.get(card.id);
    if (existing) {
      try {
        await clientApi.deleteUpload(token, existing.id);
      } catch (err) {
        console.error("Voice delete (re-record) failed:", err);
        voiceError = "Could not replace the previous recording. Try again.";
        voiceMutating = false;
        draw();
        return;
      }
      voiceUploads.delete(card.id);
      revokeVoiceUrl(card.id);
    }

    try {
      await recorder.start();
    } catch (err) {
      voiceError =
        err instanceof RecorderError
          ? err.message
          : "Could not start recording.";
      voiceMutating = false;
      draw();
      return;
    }
    // Recording is live — the mutation window (delete + start) is closed.
    voiceMutating = false;

    voiceAccumulatedMs = 0;
    voiceStartedAt = Date.now();
    clearVoiceTimer();
    // Tick the timer once a second so the mm:ss display advances.
    voiceTimer = setInterval(() => {
      if (recorder.state === "recording") draw();
    }, 1000);
    draw();
  };

  const pauseVoiceRecording = (): void => {
    if (recorder.state !== "recording") return;
    voiceAccumulatedMs += Date.now() - voiceStartedAt;
    recorder.pause();
    clearVoiceTimer();
    draw();
  };

  const resumeVoiceRecording = (): void => {
    if (recorder.state !== "paused") return;
    recorder.resume();
    voiceStartedAt = Date.now();
    clearVoiceTimer();
    voiceTimer = setInterval(() => {
      if (recorder.state === "recording") draw();
    }, 1000);
    draw();
  };

  // Stop, upload the single continuous blob as a voice upload, and cache a
  // playback URL. The card now shows the inline player.
  const stopVoiceRecording = async (): Promise<void> => {
    if (recorder.state === "idle") return;
    // Capture the card now: stop() + upload() are async, and the user may
    // navigate before they resolve. The note still belongs to THIS card.
    const capturedIndex = index;
    const card = cards[capturedIndex];
    clearVoiceTimer();

    let result;
    try {
      result = await recorder.stop();
    } catch (err) {
      console.error("Voice stop failed:", err);
      voiceError = "Could not finish the recording. Please try again.";
      voiceUploading = false;
      draw();
      return;
    }

    if (result.blob.size === 0) {
      voiceError = "Nothing was recorded. Please try again.";
      draw();
      return;
    }

    voiceUploading = true;
    voiceError = undefined;
    draw();

    let row: UploadRow;
    try {
      row = await clientApi.upload(token, card.id, result.blob, {
        kind: "voice",
        filename: `voice.${extForMime(result.mime)}`,
      });
    } catch (err) {
      console.error("Voice upload failed:", err);
      // Only surface the error if the user is still on this card.
      if (index === capturedIndex) {
        voiceError =
          err instanceof ApiError ? err.detail : "Could not save the recording.";
        voiceUploading = false;
        draw();
      }
      return;
    }

    // The note belongs to the captured card no matter where the user is now.
    voiceUploads.set(card.id, row);
    if (index === capturedIndex) {
      voiceUploading = false;
      // Local playback from the just-recorded blob — no re-download needed.
      revokeVoiceUrl(card.id);
      voiceAudioUrls.set(card.id, URL.createObjectURL(result.blob));
      draw();
    }
  };

  const deleteVoiceRecording = async (): Promise<void> => {
    if (voiceMutating) return;
    const card = cards[index];
    const existing = voiceUploads.get(card.id);
    if (!existing) return;
    voiceMutating = true;
    try {
      await clientApi.deleteUpload(token, existing.id);
    } catch (err) {
      console.error("Voice delete failed:", err);
      voiceError = "Could not delete the recording. Please try again.";
      voiceMutating = false;
      draw();
      return;
    }
    voiceUploads.delete(card.id);
    revokeVoiceUrl(card.id);
    voiceError = undefined;
    voiceMutating = false;
    draw();
  };

  const handlers: CardHandlers = {
    onConfirm: () => {
      void performSave({ kind: "confirm" });
    },
    onEditStart: () => {
      mode = "edit";
      saveError = undefined;
      draw();
    },
    onEditCancel: () => {
      mode = "view";
      draw();
    },
    onEditSubmit: (correction) => {
      void performSave({ kind: "edit", correction });
    },
    onSingleSelect: (option, note) => {
      draftSelections = new Set([option]);
      void performSave({ kind: "single-select", option, note });
    },
    onNoteSubmit: (note, option) => {
      void performSave({ kind: "note", note, option });
    },
    onMultiSelectSubmit: (options, note) => {
      void performSave({ kind: "multi-select", options, note });
    },
    onTextSubmit: (text, note) => {
      void performSave({ kind: "text", text, note });
    },
    onLinkSubmit: (url, note) => {
      void performSave({ kind: "link", url, note });
    },
    onContactSubmit: ({ name, email, role }, note) => {
      void performSave({ kind: "contact", name, email, role, note });
    },
    onFilesSelected: (files) => {
      void handleFiles(files);
    },
    onUploadRemove: (id) => {
      void removeUpload(id);
    },
    onFilesContinue: (note) => {
      void performSave({ kind: "files-continue", note });
    },
    onVoiceRecord: () => {
      void startVoiceRecording();
    },
    onVoicePause: () => {
      pauseVoiceRecording();
    },
    onVoiceResume: () => {
      resumeVoiceRecording();
    },
    onVoiceStop: () => {
      void stopVoiceRecording();
    },
    onVoiceDelete: () => {
      void deleteVoiceRecording();
    },
    onSkip: (note) => {
      void performSave({ kind: "skip", note });
    },
    onRetry: () => {
      if (pending) void performSave(pending);
    },
    onAttachmentOpen: () => {
      modalOpen = true;
      draw();
    },
    onAttachmentClose: () => {
      modalOpen = false;
      draw();
    },
    onNavBack: () => {
      navigateTo(index - 1);
    },
    onNavForward: () => {
      navigateTo(index + 1);
    },
    onNavJumpTo: (i) => {
      navigateTo(i);
    },
    onPickerOpen: () => {
      pickerOpen = true;
      draw();
    },
    onPickerClose: () => {
      pickerOpen = false;
      draw();
    },
  };

  draw();
}

function firstUnansweredIndex(
  cards: Card[],
  responses: Map<string, ClientResponse>
): number {
  for (let i = 0; i < cards.length; i++) {
    const r = responses.get(cards[i].id);
    if (!r || (r.state !== "answered" && r.state !== "skipped")) {
      return i;
    }
  }
  return cards.length;
}

// No HMR accept handler on purpose: any change to app.ts or its imports
// triggers Vite's default full page reload, which is what we want — a partial
// hot-swap would orphan the current render's button listeners. (The old
// `import.meta.hot.decline()` that forced this was removed from Vite.)

main().catch((err) => {
  console.error("Pulse boot failed:", err);
  const mount = document.getElementById("app");
  if (mount) {
    renderError(
      mount,
      "Something went wrong",
      "We could not load your session. Please refresh and try again."
    );
  }
});
