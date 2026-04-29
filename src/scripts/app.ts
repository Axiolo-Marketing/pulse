import {
  createPulseClient,
  type Card,
  type Client,
  type ClientResponse,
  type ResponseState,
} from "../lib/supabase";
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
import type { SupabaseClient } from "@supabase/supabase-js";

interface BootData {
  client: Client;
  cards: Card[];
  responses: Map<string, ClientResponse>;
  uploads: Map<string, CompletedUpload[]>; // keyed by card_id
}

interface UploadRow {
  id: string;
  card_id: string;
  client_id: string;
  file_name: string;
  file_size_bytes: number;
  storage_path: string;
  mime_type: string | null;
  uploaded_at: string;
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

  const supabase = createPulseClient(token);

  const boot = await loadBootData(supabase);
  if (!boot) {
    renderError(
      mount,
      "We could not find your engagement",
      "Please check the link or contact Tom."
    );
    return;
  }

  runApp({ mount, supabase, ...boot });
}

async function loadBootData(
  supabase: SupabaseClient
): Promise<BootData | null> {
  const { data: client, error: clientErr } = await supabase
    .from("clients")
    .select(
      "id, name, org_name, engagement_name, token, created_at, last_active_at"
    )
    .single<Client>();

  if (clientErr || !client) return null;

  const [cardsResult, responsesResult, uploadsResult] = await Promise.all([
    supabase
      .from("cards")
      .select(
        "id, client_id, order_index, category, title, context, question, response_type, options, default_value, skip_allowed, attachment_path, created_at"
      )
      .eq("client_id", client.id)
      .order("order_index", { ascending: true }),
    supabase
      .from("responses")
      .select(
        "id, card_id, client_id, state, response_value, viewed_at, answered_at, created_at, updated_at"
      )
      .eq("client_id", client.id),
    supabase
      .from("uploads")
      .select(
        "id, card_id, client_id, file_name, file_size_bytes, storage_path, mime_type, uploaded_at"
      )
      .eq("client_id", client.id)
      .order("uploaded_at", { ascending: true }),
  ]);

  if (cardsResult.error || !cardsResult.data) return null;

  const cards = cardsResult.data as Card[];
  const responses = new Map<string, ClientResponse>(
    (responsesResult.data ?? []).map((r) => {
      const cr = r as ClientResponse;
      return [cr.card_id, cr];
    })
  );

  const uploads = new Map<string, CompletedUpload[]>();
  for (const row of (uploadsResult.data ?? []) as UploadRow[]) {
    const list = uploads.get(row.card_id) ?? [];
    list.push({ id: row.id, name: row.file_name, sizeBytes: row.file_size_bytes });
    uploads.set(row.card_id, list);
  }

  return { client, cards, responses, uploads };
}

interface RunCtx extends BootData {
  mount: HTMLElement;
  supabase: SupabaseClient;
}

function runApp(ctx: RunCtx): void {
  const { mount, supabase, client, cards, responses, uploads } = ctx;

  let index = firstUnansweredIndex(cards, responses);
  let mode: CardMode = "view";
  let saveError: string | undefined;
  let modalOpen = false;
  let pending: PendingAction | undefined;

  // Per-card UI scratch state. Reset whenever the card changes.
  let draftSelections: Set<string> = new Set();
  let pendingUploads: PendingUpload[] = [];

  type PendingAction =
    | { kind: "confirm" }
    | { kind: "edit"; correction: string }
    | { kind: "skip" }
    | { kind: "single-select"; option: string }
    | { kind: "multi-select"; options: string[] }
    | { kind: "text"; text: string }
    | { kind: "link"; url: string }
    | { kind: "contact"; name: string; email: string; role: string }
    | { kind: "files-continue" };

  const draw = (): void => {
    if (index >= cards.length) {
      renderComplete(mount, client.name);
      return;
    }
    const card = cards[index];
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
      draftSelections,
      handlers,
    });
  };

  const advance = (): void => {
    index += 1;
    mode = "view";
    saveError = undefined;
    pending = undefined;
    modalOpen = false;
    draftSelections = new Set();
    pendingUploads = [];
    draw();
  };

  const performSave = async (action: PendingAction): Promise<void> => {
    pending = action;
    saveError = undefined;
    mode = "saving";
    draw();

    const card = cards[index];

    let state: ResponseState;
    let value: unknown;
    let answeredAt: string | null;

    switch (action.kind) {
      case "confirm":
        state = "answered";
        value = { confirmed: true };
        answeredAt = new Date().toISOString();
        break;
      case "edit":
        state = "answered";
        value = { confirmed: false, correction: action.correction };
        answeredAt = new Date().toISOString();
        break;
      case "skip":
        state = "skipped";
        value = null;
        answeredAt = null;
        break;
      case "single-select":
        state = "answered";
        value = { selected: action.option };
        answeredAt = new Date().toISOString();
        break;
      case "multi-select":
        state = "answered";
        value = { selected: action.options };
        answeredAt = new Date().toISOString();
        break;
      case "text":
        state = "answered";
        value = { text: action.text };
        answeredAt = new Date().toISOString();
        break;
      case "link":
        state = "answered";
        value = { url: action.url };
        answeredAt = new Date().toISOString();
        break;
      case "contact":
        state = "answered";
        value = {
          name: action.name,
          email: action.email,
          role: action.role,
        };
        answeredAt = new Date().toISOString();
        break;
      case "files-continue": {
        const list = uploads.get(card.id) ?? [];
        state = list.length > 0 ? "answered" : "skipped";
        value = list.length > 0 ? { file_ids: list.map((u) => u.id) } : null;
        answeredAt = list.length > 0 ? new Date().toISOString() : null;
        break;
      }
    }

    const { data, error } = await supabase
      .from("responses")
      .upsert(
        {
          card_id: card.id,
          client_id: client.id,
          state,
          response_value: value,
          answered_at: answeredAt,
        },
        { onConflict: "card_id,client_id" }
      )
      .select()
      .single<ClientResponse>();

    if (error || !data) {
      console.error("Save failed:", error);
      saveError = "Could not save just now. Tap Retry.";
      mode = "view";
      draw();
      return;
    }

    responses.set(card.id, data);

    supabase
      .from("clients")
      .update({ last_active_at: new Date().toISOString() })
      .eq("id", client.id)
      .then(({ error: touchErr }) => {
        if (touchErr) console.warn("last_active_at touch failed:", touchErr);
      });

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

      const path = `${client.id}/${card.id}/${tempId}-${sanitizeName(file.name)}`;

      const { error: uploadErr } = await supabase.storage
        .from("pulse-uploads")
        .upload(path, file, {
          contentType: file.type || "application/octet-stream",
          upsert: false,
        });

      if (uploadErr) {
        console.error("Storage upload failed:", uploadErr);
        pendingUploads = pendingUploads.map((p) =>
          p.tempId === tempId ? { ...p, error: "Upload failed" } : p
        );
        draw();
        continue;
      }

      const { data: row, error: insertErr } = await supabase
        .from("uploads")
        .insert({
          card_id: card.id,
          client_id: client.id,
          file_name: file.name,
          file_size_bytes: file.size,
          storage_path: path,
          mime_type: file.type || null,
        })
        .select()
        .single<UploadRow>();

      if (insertErr || !row) {
        console.error("Uploads insert failed:", insertErr);
        pendingUploads = pendingUploads.map((p) =>
          p.tempId === tempId
            ? { ...p, error: "Could not register upload" }
            : p
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

    const { error: rowErr } = await supabase
      .from("uploads")
      .delete()
      .eq("id", uploadId);
    if (rowErr) {
      console.error("Upload row delete failed:", rowErr);
      return;
    }

    uploads.set(
      card.id,
      list.filter((u) => u.id !== uploadId)
    );
    draw();

    // Best-effort storage cleanup. The row is gone either way, so a stray
    // object is harmless.
    void supabase.storage
      .from("pulse-uploads")
      .remove([`${client.id}/${card.id}/${target.id}`]);
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
    onSingleSelect: (option) => {
      draftSelections = new Set([option]);
      void performSave({ kind: "single-select", option });
    },
    onMultiSelectSubmit: (options) => {
      void performSave({ kind: "multi-select", options });
    },
    onTextSubmit: (text) => {
      void performSave({ kind: "text", text });
    },
    onLinkSubmit: (url) => {
      void performSave({ kind: "link", url });
    },
    onContactSubmit: ({ name, email, role }) => {
      void performSave({ kind: "contact", name, email, role });
    },
    onFilesSelected: (files) => {
      void handleFiles(files);
    },
    onUploadRemove: (id) => {
      void removeUpload(id);
    },
    onFilesContinue: () => {
      void performSave({ kind: "files-continue" });
    },
    onSkip: () => {
      void performSave({ kind: "skip" });
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

function sanitizeName(name: string): string {
  // Storage keys must be safe for URLs. Replace anything that would need
  // encoding with '_', and collapse runs.
  return name.replace(/[^A-Za-z0-9._-]/g, "_").replace(/_+/g, "_");
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
