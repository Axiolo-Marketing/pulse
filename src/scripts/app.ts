import {
  clientApi,
  ApiError,
  type Card,
  type Client,
  type ClientResponse,
  type ResponseState,
} from "../lib/api";
import {
  renderCard,
  renderComplete,
  renderError,
  renderLoading,
  type CardHandlers,
  type CardMode,
  type CompletedUpload,
  type PendingUpload,
} from "../lib/render";

interface BootData {
  client: Client;
  cards: Card[];
  responses: Map<string, ClientResponse>;
  uploads: Map<string, CompletedUpload[]>; // keyed by card_id
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

  runApp({ mount, token, ...boot });
}

async function loadBootData(token: string): Promise<BootData | null> {
  let client: Client;
  try {
    client = await clientApi.me(token);
  } catch (err) {
    if (err instanceof ApiError) return null;
    throw err;
  }

  const [cards, responsesList, uploadsList] = await Promise.all([
    clientApi.cards(token),
    clientApi.responses(token),
    clientApi.uploads(token),
  ]);

  const responses = new Map<string, ClientResponse>(
    responsesList.map((r) => [r.card_id, r])
  );

  const uploads = new Map<string, CompletedUpload[]>();
  for (const row of uploadsList) {
    const list = uploads.get(row.card_id) ?? [];
    list.push({
      id: row.id,
      name: row.file_name,
      sizeBytes: row.file_size_bytes,
    });
    uploads.set(row.card_id, list);
  }

  return { client, cards, responses, uploads };
}

interface RunCtx extends BootData {
  mount: HTMLElement;
  token: string;
}

function runApp(ctx: RunCtx): void {
  const { mount, token, client, cards, responses, uploads } = ctx;

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
  // (card_id, client_id) unique constraint — safe to fire on every render.
  const markViewed = (cardId: string): void => {
    if (responses.has(cardId)) return;
    clientApi
      .markViewed(token, cardId)
      .then((data) => responses.set(cardId, data))
      .catch((err) => console.warn("mark viewed failed:", err));
  };

  const draw = (): void => {
    if (index >= cards.length) {
      renderComplete(mount, client.name);
      return;
    }
    const card = cards[index];
    if (mode === "view") markViewed(card.id);
    renderCard(mount, {
      card,
      position: index + 1,
      total: cards.length,
      mode,
      saveError,
      baseUrl: BASE_URL,
      uploads: uploads.get(card.id) ?? [],
      pending: pendingUploads,
      modalOpen,
      pickerOpen,
      draftSelections,
      showResume,
      existingResponse: responses.get(card.id),
      cards,
      responses,
      handlers,
    });
  };

  const navigateTo = (newIndex: number): void => {
    if (newIndex < 0 || newIndex > cards.length) return;
    if (newIndex === index && !pickerOpen) return;
    clearRetryTimer();
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

  const performSave = async (action: PendingAction): Promise<void> => {
    clearRetryTimer();
    pending = action;
    saveError = undefined;
    mode = "saving";
    draw();

    const card = cards[index];

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

    // The backend derives `client_id` from the request's token and sets
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

    // Best-effort heartbeat — errors are non-fatal.
    clientApi
      .heartbeat(token)
      .catch((err) => console.warn("last_active_at touch failed:", err));

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

if (import.meta.hot) {
  // Refuse hot-replacement for this module entirely. Any change to app.ts
  // or its imports forces Vite to do a full page reload, which discards
  // every render's button listeners along with the old DOM.
  import.meta.hot.decline();
}

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
