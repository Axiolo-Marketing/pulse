import { API_BASE, type Card, type ClientResponse } from "./api";

// Extensions that should render via <img>. Everything else (html, htm,
// pdf) falls back to the sandboxed iframe path that already exists.
const IMAGE_EXTS = new Set([".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"]);

function attachmentExt(path: string): string {
  const dot = path.lastIndexOf(".");
  return dot >= 0 ? path.slice(dot).toLowerCase() : "";
}

function attachmentSrc(path: string, baseUrl: string): string {
  // Uploaded attachments live behind the API. The backend route is
  // /api/attachments/<filename>; the stored attachment_path is
  // `attachments/<uuid>.<ext>`. Static-deliverable paths
  // (`deliverables/...`) keep the existing same-origin behavior.
  if (path.startsWith("attachments/")) {
    const filename = path.slice("attachments/".length);
    return `${API_BASE}/api/attachments/${filename}`;
  }
  return baseUrl.endsWith("/") ? baseUrl + path : `${baseUrl}/${path}`;
}

// Tiny escape helper so we can build cards via template strings without
// pulling in a framework. Card text comes from our own database, but we
// also reflect it back into the DOM, so escape on the way out.
const escape = (s: string): string =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

const escapeAttr = (s: string): string => escape(s);

export function renderLoading(mount: HTMLElement): void {
  mount.innerHTML = `<div class="loading">Loading...</div>`;
}

export function renderError(
  mount: HTMLElement,
  title: string,
  body: string
): void {
  mount.innerHTML = `
    <div class="error" role="alert">
      <div class="error-mark">!</div>
      <h1 class="error-title">${escape(title)}</h1>
      <p class="error-body">${escape(body)}</p>
    </div>
  `;
}

export function renderComplete(
  mount: HTMLElement,
  name: string,
  onReview?: () => void
): void {
  mount.innerHTML = `
    <div class="card complete" role="status">
      <div class="category">Thank you</div>
      <h1 class="card-title">All done, ${escape(firstName(name))}</h1>
      <hr class="divider" />
      <p class="context">
        Your responses are with Tom. He will follow up directly.
      </p>
      ${
        onReview
          ? `<p class="review-hint">Need to change something?
              <button type="button" class="review-link" data-action="review">Review or edit your answers</button>
            </p>`
          : ""
      }
    </div>
  `;

  if (onReview) {
    mount
      .querySelector<HTMLButtonElement>('[data-action="review"]')
      ?.addEventListener("click", onReview);
  }
}

function firstName(full: string): string {
  return full.split(" ")[0] ?? full;
}

/** `"waiting"`: reactive cards, right after a qualifying correction save —
 * the deck stays parked on the corrected card instead of advancing, showing
 * a quiet "reviewing your correction…" status while it waits (briefly) to
 * see if a follow-up lands. No inputs/buttons render in this mode at all
 * (see `renderWaitingBody`), so there's nothing to disable. */
export type CardMode = "view" | "edit" | "saving" | "waiting";

export interface PendingUpload {
  tempId: string;
  name: string;
  sizeBytes: number;
  progress: number; // 0..1
  error?: string;
}

export interface CompletedUpload {
  id: string;
  name: string;
  sizeBytes: number;
}

/** UI state for the per-card voice recorder, driven by `app.ts`. The deck
 * shows one of four visual states off `phase`:
 *   - `idle`      → a "Record" button (no recording yet).
 *   - `recording` → a running mm:ss timer + Pause + Stop.
 *   - `paused`    → a frozen timer + Resume + Stop.
 *   - `done`      → an inline `<audio>` of the saved take + Re-record/Delete.
 * `uploading` flips while the stopped blob is in flight. `error` surfaces
 * mic-permission and upload failures inline near the button. */
export interface VoiceState {
  phase: "idle" | "recording" | "paused" | "uploading" | "done";
  /** Elapsed seconds shown in the timer (recording/paused). */
  elapsedSeconds: number;
  /** Object URL for inline playback once a take is saved. */
  audioUrl?: string;
  error?: string;
}

export interface CardHandlers {
  // confirm-edit (no note field — uses Needs edit textarea instead)
  onConfirm: () => void;
  onEditStart: () => void;
  onEditCancel: () => void;
  onEditSubmit: (correction: string) => void;
  // typed inputs (each carries an optional free-form note)
  onSingleSelect: (option: string, note?: string) => void;
  onMultiSelectSubmit: (options: string[], note?: string) => void;
  onTextSubmit: (text: string, note?: string) => void;
  onLinkSubmit: (url: string, note?: string) => void;
  onContactSubmit: (
    c: { name: string; email: string; role: string },
    note?: string
  ) => void;
  // file upload
  onFilesSelected: (files: FileList) => void;
  onUploadRemove: (uploadId: string) => void;
  onFilesContinue: (note?: string) => void;
  // voice answer (record control; only rendered when the engagement has
  // voice enabled — see `RenderCardArgs.voiceEnabled`)
  onVoiceRecord: () => void;
  onVoicePause: () => void;
  onVoiceResume: () => void;
  onVoiceStop: () => void;
  onVoiceDelete: () => void;
  // shared
  onSkip: (note?: string) => void;
  onRetry: () => void;
  // attachment modal
  onAttachmentOpen: () => void;
  onAttachmentClose: () => void;
  // navigation
  onNavBack: () => void;
  onNavForward: () => void;
  onNavJumpTo: (index: number) => void;
  onPickerOpen: () => void;
  onPickerClose: () => void;
}

export interface RenderCardArgs {
  card: Card;
  position: number;
  total: number;
  mode: CardMode;
  saveError?: string;
  baseUrl: string;
  uploads: CompletedUpload[];
  pending: PendingUpload[];
  /** Voice-recorder UI state for this card. Only consulted when
   * `voiceEnabled` is true; otherwise no voice UI renders at all. */
  voice: VoiceState;
  /** Whether this engagement has voice recording enabled (per-engagement
   * toggle, default off). When false, no voice control — record button or
   * playback — renders on any card. */
  voiceEnabled: boolean;
  modalOpen: boolean;
  pickerOpen?: boolean;
  draftSelections?: Set<string>;
  showResume?: boolean;
  existingResponse?: ClientResponse;
  cards?: Card[];
  responses?: Map<string, ClientResponse>;
  handlers: CardHandlers;
  /** Optional `<img src>` for the operator's org logo. When supplied
   * (caller resolved it from `Engagement.org_logo_path`), the top-bar
   * renders this in place of the default Axiolo wordmark — that's the
   * client-facing piece of the org branding work. Omitted by callers
   * that don't have logo data — the deck falls back to the Axiolo
   * wordmark and the existing layout is unchanged. */
  orgLogoSrc?: string | null;
  /** Optional human-readable org name (shown next to or instead of
   * "Pulse" in the brand row). Falls back to the Pulse wordmark. */
  orgName?: string | null;
  /** Reactive cards: a quiet one-line status shown under the progress area
   * while a background generation poll is active for the current session
   * ("Checking if we need a quick follow-up…"). `undefined` renders
   * nothing — never an alert/confirm, just an inline status line. */
  pollHint?: string;
}

export function renderCard(mount: HTMLElement, args: RenderCardArgs): void {
  const {
    card,
    position,
    total,
    mode,
    saveError,
    baseUrl,
    modalOpen,
  } = args;

  const banner = saveError
    ? `<div class="save-banner" role="alert">
         <span>${escape(saveError)}</span>
         <button class="banner-retry" type="button" data-action="retry">Retry</button>
       </div>`
    : args.showResume
    ? `<div class="resume-banner" role="status">
         Welcome back. Picking up where you left off.
       </div>`
    : "";

  const body =
    mode === "edit" && card.response_type === "confirm-edit"
      ? renderEditBody(card)
      : mode === "waiting"
      ? renderWaitingBody(card, args.existingResponse)
      : renderViewBody(card, mode, args);

  const attachmentBtn = card.attachment_path
    ? `<button class="btn-link" type="button" data-action="open-attachment">
         View Active Reference
       </button>`
    : "";

  const modal =
    modalOpen && card.attachment_path
      ? renderModal(card, baseUrl)
      : "";

  const picker =
    args.pickerOpen && args.cards && args.responses
      ? renderPicker(args.cards, args.responses, args.position - 1)
      : "";

  const backDisabled = position === 1 ? "disabled" : "";
  const forwardDisabled = position === total ? "disabled" : "";

  const pollHintHtml = args.pollHint
    ? `<div class="poll-hint" role="status">${escape(args.pollHint)}</div>`
    : "";

  // Brand row: the Pulse product wordmark first, optionally followed by
  // the operator's org logo (renders as `Pulse · [org logo]`). The Axiolo
  // wordmark lives in the page footer ("Made by Axiolo"). With no org logo
  // the row is just the Pulse wordmark.
  const orgBrandHtml = args.orgLogoSrc
    ? `<span class="brand-sep" aria-hidden="true">·</span>
       <img src="${escapeAttr(args.orgLogoSrc)}" alt="${escapeAttr(args.orgName ?? "Organization")}" class="brand-logo brand-logo--org" width="40" height="40" />`
    : "";

  mount.innerHTML = `
    <header class="topbar">
      <span class="brand">
        <span class="brand-mark">Pulse</span>
        ${orgBrandHtml}
      </span>
      <nav class="nav-controls" aria-label="Card navigation">
        <button class="nav-arrow" type="button" data-action="nav-back" ${backDisabled} aria-label="Previous card">‹</button>
        <button class="progress-btn" type="button" data-action="picker-open" aria-haspopup="dialog">
          ${position} of ${total}
          <span class="progress-caret" aria-hidden="true">▾</span>
        </button>
        <button class="nav-arrow" type="button" data-action="nav-forward" ${forwardDisabled} aria-label="Next card">›</button>
      </nav>
    </header>
    ${pollHintHtml}
    ${banner}
    <article class="card" aria-labelledby="card-title">
      <div class="category">${escape(card.category)}</div>
      <h1 class="card-title" id="card-title">${escape(card.title)}</h1>
      <hr class="divider" />
      <p class="context">${escape(card.context)}</p>
      ${attachmentBtn}
      ${body}
    </article>
    ${modal}
    ${picker}
  `;

  attachHandlers(mount, args);
}

function renderPicker(
  cards: Card[],
  responses: Map<string, ClientResponse>,
  currentIndex: number
): string {
  const items = cards
    .map((c, i) => {
      const r = responses.get(c.id);
      const stateClass = pickerStateClass(r);
      const stateLabel = pickerStateLabel(r);
      const current = i === currentIndex ? " is-current" : "";
      return `
        <button
          class="picker-item${current}"
          type="button"
          data-action="picker-jump"
          data-index="${i}"
        >
          <span class="picker-num">${i + 1}.</span>
          <span class="picker-title">${escape(c.title)}</span>
          <span class="picker-state ${stateClass}">${escape(stateLabel)}</span>
        </button>
      `;
    })
    .join("");

  return `
    <div class="picker" role="dialog" aria-modal="true" aria-label="Jump to card">
      <div class="picker-backdrop" data-action="picker-close"></div>
      <div class="picker-panel">
        <header class="picker-header">
          <span class="picker-heading">Jump to card</span>
          <button class="picker-close" type="button" data-action="picker-close" aria-label="Close">×</button>
        </header>
        <div class="picker-list">${items}</div>
      </div>
    </div>
  `;
}

function pickerStateClass(r: ClientResponse | undefined): string {
  if (!r) return "is-pending";
  switch (r.state) {
    case "answered": return "is-answered";
    case "skipped":  return "is-skipped";
    case "viewed":   return "is-viewed";
    default:         return "is-pending";
  }
}

function pickerStateLabel(r: ClientResponse | undefined): string {
  if (!r) return "Not viewed";
  switch (r.state) {
    case "answered": return "Answered";
    case "skipped":  return "Skipped";
    case "viewed":   return "Viewed";
    default:         return "Not viewed";
  }
}

function renderViewBody(
  card: Card,
  mode: CardMode,
  args: RenderCardArgs
): string {
  const saving = mode === "saving";
  return `
    <p class="question">${escape(card.question)}</p>
    ${renderPriorHint(card, args.existingResponse)}
    ${renderInput(card, saving, args)}
    ${args.voiceEnabled ? renderVoiceControl(args.voice, saving) : ""}
    ${renderNoteField(card, saving, args.existingResponse)}
    <div class="actions">${renderActions(card, saving, args)}</div>
  `;
}

function formatTimer(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

// Per-card voice recorder. Rendered below the primary input on every card
// of a voice-enabled engagement (gated by `RenderCardArgs.voiceEnabled` —
// when off, this isn't called at all). A voice note supplements the typed
// answer; a voice-only answer still counts as answered (app.ts forces
// state='answered' when one is present).
function renderVoiceControl(voice: VoiceState, saving: boolean): string {
  const errorHtml = voice.error
    ? `<p class="voice-error" role="alert">${escape(voice.error)}</p>`
    : "";

  if (voice.phase === "recording" || voice.phase === "paused") {
    const paused = voice.phase === "paused";
    const toggle = paused
      ? `<button class="voice-btn voice-resume" type="button" data-action="voice-resume">
           <span class="voice-icon" aria-hidden="true">▶</span> Resume
         </button>`
      : `<button class="voice-btn voice-pause" type="button" data-action="voice-pause">
           <span class="voice-icon" aria-hidden="true">❚❚</span> Pause
         </button>`;
    return `
      <div class="voice-control is-active ${paused ? "is-paused" : "is-recording"}">
        <div class="voice-status" role="status">
          <span class="voice-dot" aria-hidden="true"></span>
          <span class="voice-state-label">${paused ? "Paused" : "Recording"}</span>
          <span class="voice-timer">${formatTimer(voice.elapsedSeconds)}</span>
        </div>
        <div class="voice-actions">
          ${toggle}
          <button class="voice-btn voice-stop" type="button" data-action="voice-stop">
            <span class="voice-icon" aria-hidden="true">■</span> Stop
          </button>
        </div>
      </div>
      ${errorHtml}
    `;
  }

  if (voice.phase === "uploading") {
    return `
      <div class="voice-control is-uploading">
        <span class="voice-uploading-label">Saving recording…</span>
      </div>
      ${errorHtml}
    `;
  }

  if (voice.phase === "done" && voice.audioUrl) {
    return `
      <div class="voice-control is-done">
        <audio class="voice-player" controls preload="metadata" src="${escapeAttr(voice.audioUrl)}"></audio>
        <div class="voice-actions">
          <button class="voice-btn voice-rerecord" type="button" data-action="voice-record" ${saving ? "disabled" : ""}>
            <span class="voice-icon" aria-hidden="true">●</span> Re-record
          </button>
          <button class="voice-btn voice-delete" type="button" data-action="voice-delete" ${saving ? "disabled" : ""}>Delete</button>
        </div>
      </div>
      ${errorHtml}
    `;
  }

  // idle — a quiet supplement, never a peer of the primary answer actions
  return `
    <div class="voice-control is-idle">
      <button class="voice-btn voice-record" type="button" data-action="voice-record" ${saving ? "disabled" : ""} aria-label="Add a voice note">
        <span class="voice-icon" aria-hidden="true">●</span> Add a voice note
      </button>
    </div>
    ${errorHtml}
  `;
}

// Optional free-form note field. Surfaced only on cards that don't
// already have an open-text input — those cards reuse the same
// "talk or type" placeholder on their primary input instead.
function renderNoteField(
  card: Card,
  saving: boolean,
  prior: ClientResponse | undefined
): string {
  if (
    card.response_type === "confirm-edit" ||
    card.response_type === "short-text" ||
    card.response_type === "long-text"
  ) {
    return "";
  }
  const v = (prior?.response_value ?? {}) as { note?: string };
  const prefill = v.note ?? "";
  const dis = saving ? "disabled" : "";
  return `
    <label class="note-field">
      <span class="note-label">Notes (optional)</span>
      <textarea
        id="note-input"
        class="textarea note-textarea"
        rows="2"
        placeholder="${VOICE_PLACEHOLDER}"
        ${dis}
      >${escape(prefill)}</textarea>
    </label>
  `;
}

const VOICE_PLACEHOLDER = "Add a note. Tap the keyboard mic to talk.";

// Surface the user's prior choice when they navigate back to a card so they
// know the answer is already on file. The form below stays editable; saving
// again upserts the row.
function renderPriorHint(
  card: Card,
  prior: ClientResponse | undefined
): string {
  if (!prior || prior.state === "not_started" || prior.state === "viewed") {
    return "";
  }
  if (prior.state === "skipped") {
    return `<div class="prior-hint">You skipped this earlier. Answer if you want to revisit.</div>`;
  }
  if (prior.state !== "answered") return "";
  if (card.response_type === "confirm-edit") {
    const v = (prior.response_value ?? {}) as { confirmed?: boolean };
    return v.confirmed
      ? `<div class="prior-hint">You confirmed this earlier.</div>`
      : `<div class="prior-hint">You sent edits earlier.</div>`;
  }
  return `<div class="prior-hint">Your previous answer is loaded. Edit and resubmit to update it.</div>`;
}

function renderInput(
  card: Card,
  saving: boolean,
  args: RenderCardArgs
): string {
  const prior = args.existingResponse;
  const v = (prior?.response_value ?? {}) as {
    text?: string;
    url?: string;
    correction?: string;
    name?: string;
    email?: string;
    role?: string;
  };
  switch (card.response_type) {
    case "single-select":
      return renderSingleSelect(card, args.draftSelections, saving);
    case "multi-select":
      return renderMultiSelect(card, args.draftSelections, saving);
    case "short-text":
      return renderShortText(saving, v.text);
    case "long-text":
      return renderLongText(saving, v.text);
    case "document-link":
      return renderDocumentLink(saving, v.url);
    case "contact-share":
      return renderContactShare(saving, v.name, v.email, v.role);
    case "file-upload":
      return renderFileUpload(card, saving, args);
    case "confirm-edit":
    default:
      return "";
  }
}

function renderSingleSelect(
  card: Card,
  selections: Set<string> | undefined,
  saving: boolean
): string {
  const opts = card.options ?? [];
  const dis = saving ? "disabled" : "";
  return `
    <div class="options" role="radiogroup">
      ${opts
        .map((o) => {
          const selected = selections?.has(o) ?? false;
          return `<button
            class="option ${selected ? "selected" : ""}"
            type="button"
            role="radio"
            aria-checked="${selected}"
            data-action="toggle-single"
            data-option="${escapeAttr(o)}"
            ${dis}
          >${escape(o)}</button>`;
        })
        .join("")}
    </div>
  `;
}

function renderMultiSelect(
  card: Card,
  selections: Set<string> | undefined,
  saving: boolean
): string {
  const opts = card.options ?? [];
  const dis = saving ? "disabled" : "";
  return `
    <div class="options" role="group">
      ${opts
        .map((o) => {
          const selected = selections?.has(o) ?? false;
          return `<button
            class="option ${selected ? "selected" : ""}"
            type="button"
            role="checkbox"
            aria-checked="${selected}"
            data-action="toggle-multi"
            data-option="${escapeAttr(o)}"
            ${dis}
          >
            <span class="option-mark">${selected ? "✓" : ""}</span>
            <span class="option-text">${escape(o)}</span>
          </button>`;
        })
        .join("")}
    </div>
  `;
}

function renderShortText(saving: boolean, prefill?: string): string {
  const dis = saving ? "disabled" : "";
  const value = prefill ? `value="${escapeAttr(prefill)}"` : "";
  return `<input id="text-input" class="input" type="text" placeholder="${VOICE_PLACEHOLDER}" ${value} ${dis} />`;
}

function renderLongText(saving: boolean, prefill?: string): string {
  const dis = saving ? "disabled" : "";
  return `<textarea id="text-input" class="textarea" rows="5" placeholder="${VOICE_PLACEHOLDER}" ${dis}>${escape(prefill ?? "")}</textarea>`;
}

function renderDocumentLink(saving: boolean, prefill?: string): string {
  const dis = saving ? "disabled" : "";
  const value = prefill ? `value="${escapeAttr(prefill)}"` : "";
  return `<input id="link-input" class="input" type="url" inputmode="url" placeholder="https://..." ${value} ${dis} />`;
}

function renderContactShare(
  saving: boolean,
  name?: string,
  email?: string,
  role?: string
): string {
  const dis = saving ? "disabled" : "";
  const v = (s?: string) => (s ? `value="${escapeAttr(s)}"` : "");
  return `
    <div class="contact-fields">
      <input id="c-name" class="input" type="text" placeholder="Name" ${v(name)} ${dis} />
      <input id="c-email" class="input" type="email" inputmode="email" placeholder="Email" ${v(email)} ${dis} />
      <input id="c-role" class="input" type="text" placeholder="Role" ${v(role)} ${dis} />
    </div>
  `;
}

function renderFileUpload(
  _card: Card,
  saving: boolean,
  args: RenderCardArgs
): string {
  const dis = saving ? "disabled" : "";
  const completed = args.uploads;
  const pending = args.pending;
  const totalCount = completed.length + pending.length;
  const max = 5;
  const remaining = Math.max(0, max - totalCount);

  const dropZone =
    remaining > 0
      ? `<label class="dropzone ${dis ? "is-disabled" : ""}">
           <input
             type="file"
             multiple
             accept=".pdf,.docx,.png,.jpg,.jpeg,.csv,.xlsx,application/pdf,image/*,text/csv"
             data-action="files-selected"
             ${dis}
           />
           <span class="dropzone-label">
             Tap to upload or drop files here
           </span>
           <span class="dropzone-hint">
             Up to ${remaining} more file${remaining === 1 ? "" : "s"}, max 25MB each
           </span>
         </label>`
      : `<div class="dropzone is-full">
           Maximum of ${max} files reached. Remove one to add another.
         </div>`;

  const chips = [
    ...completed.map(
      (u) => `
      <div class="file-chip" data-upload-id="${escapeAttr(u.id)}">
        <span class="file-name">${escape(u.name)}</span>
        <span class="file-size">${formatSize(u.sizeBytes)}</span>
        <button class="file-remove" type="button" data-action="remove-upload" data-upload-id="${escapeAttr(u.id)}" aria-label="Remove">×</button>
      </div>
    `
    ),
    ...pending.map(
      (p) => `
      <div class="file-chip is-pending">
        <span class="file-name">${escape(p.name)}</span>
        <span class="file-size">${
          p.error ? `<span class="file-error">${escape(p.error)}</span>` : `${Math.round(p.progress * 100)}%`
        }</span>
      </div>
    `
    ),
  ].join("");

  return `
    ${dropZone}
    ${chips ? `<div class="file-list">${chips}</div>` : ""}
  `;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderActions(
  card: Card,
  saving: boolean,
  args: RenderCardArgs
): string {
  const dis = saving ? "disabled" : "";
  const skipBtn = card.skip_allowed
    ? `<button class="btn btn-tertiary" type="button" data-action="skip" ${dis}>Skip for now</button>`
    : "";

  const savingLabel = saving ? "Saving..." : null;

  switch (card.response_type) {
    case "confirm-edit":
      return `
        <button class="btn btn-primary" type="button" data-action="confirm" ${dis}>${
          savingLabel ?? "Yes, correct"
        }</button>
        <button class="btn btn-secondary" type="button" data-action="edit-start" ${dis}>Needs edit</button>
        ${skipBtn}
      `;
    case "single-select":
      // Single-select auto-saves on tap; no Continue button needed.
      return skipBtn;
    case "multi-select":
      return `
        <button class="btn btn-primary" type="button" data-action="multi-submit" ${dis}>${
          savingLabel ?? "Continue"
        }</button>
        ${skipBtn}
      `;
    case "short-text":
    case "long-text":
      return `
        <button class="btn btn-primary" type="button" data-action="text-submit" ${dis}>${
          savingLabel ?? "Submit"
        }</button>
        ${skipBtn}
      `;
    case "document-link":
      return `
        <button class="btn btn-primary" type="button" data-action="link-submit" ${dis}>${
          savingLabel ?? "Submit"
        }</button>
        ${skipBtn}
      `;
    case "contact-share":
      return `
        <button class="btn btn-primary" type="button" data-action="contact-submit" ${dis}>${
          savingLabel ?? "Share contact"
        }</button>
        ${skipBtn}
      `;
    case "file-upload": {
      const hasFiles = args.uploads.length > 0;
      const hasPending = args.pending.some((p) => !p.error);
      // A voice-only answer still counts — don't trap the user behind a
      // disabled Continue when they recorded but uploaded no files.
      const hasVoice = args.voice.phase === "done";
      const continueDisabled =
        saving || hasPending || (!hasFiles && !hasVoice);
      return `
        <button class="btn btn-primary" type="button" data-action="files-continue" ${
          continueDisabled ? "disabled" : ""
        }>${savingLabel ?? "Continue"}</button>
        ${skipBtn}
      `;
    }
    default:
      return "";
  }
}

function renderEditBody(card: Card): string {
  // Textarea opens blank. We deliberately do not pre-fill from default_value
  // or any prior correction — operators don't want prompted content nudging
  // the client toward a specific phrasing.
  const placeholder = "What should we update? A short note is fine.";
  return `
    <p class="question">${escape(card.question)}</p>
    <textarea
      id="correction"
      class="textarea"
      rows="5"
      placeholder="${escape(placeholder)}"
      autofocus
    ></textarea>
    <div class="actions">
      <button class="btn btn-primary" type="button" data-action="edit-submit">Save changes</button>
      <button class="btn btn-tertiary" type="button" data-action="edit-cancel">Cancel</button>
    </div>
  `;
}

// Reactive cards: shown in place of the normal answer body right after a
// qualifying correction save, while app.ts waits (briefly) to see if the
// correction kicked off a follow-up — see `startFollowUpPoll`/
// `beginAwaitFollowUp` in app.ts. No actions render at all here (nothing to
// disable, nothing to double-submit); navigation (back/forward/picker) stays
// live in the header and cancels the wait if the respondent uses it.
function renderWaitingBody(
  card: Card,
  prior: ClientResponse | undefined
): string {
  return `
    <p class="question">${escape(card.question)}</p>
    ${renderPriorHint(card, prior)}
    <div class="waiting-status" role="status">
      <span class="waiting-dot" aria-hidden="true"></span>
      One moment — reviewing your correction…
    </div>
  `;
}

function renderModal(card: Card, baseUrl: string): string {
  const path = card.attachment_path ?? "";
  const src = attachmentSrc(path, baseUrl);
  const isImage = IMAGE_EXTS.has(attachmentExt(path));
  const viewer = isImage
    ? `<img
         class="modal-image"
         src="${escapeAttr(src)}"
         alt="${escapeAttr(card.title)} reference"
         loading="lazy"
       />`
    : `<iframe
         class="modal-iframe"
         src="${escapeAttr(src)}"
         sandbox="allow-scripts"
         title="${escapeAttr(card.title)} reference"
         loading="lazy"
       ></iframe>`;
  return `
    <div class="modal" role="dialog" aria-modal="true" aria-label="${escapeAttr(card.title)}">
      <div class="modal-backdrop" data-action="close-attachment"></div>
      <div class="modal-panel">
        <header class="modal-header">
          <span class="modal-title">${escape(card.title)} reference</span>
          <button class="modal-close" type="button" data-action="close-attachment" aria-label="Close">×</button>
        </header>
        ${viewer}
      </div>
    </div>
  `;
}

function attachHandlers(mount: HTMLElement, args: RenderCardArgs): void {
  const { handlers } = args;

  for (const btn of mount.querySelectorAll<HTMLButtonElement>(
    "button[data-action]"
  )) {
    if (btn.disabled) continue;
    const action = btn.dataset.action;
    btn.addEventListener("click", () => dispatch(mount, action, btn, handlers));
  }

  // File input is not a <button>, so it needs its own listener.
  for (const input of mount.querySelectorAll<HTMLInputElement>(
    "input[type='file'][data-action='files-selected']"
  )) {
    if (input.disabled) continue;
    input.addEventListener("change", () => {
      if (input.files && input.files.length > 0) {
        handlers.onFilesSelected(input.files);
        input.value = "";
      }
    });
  }

  // Backdrop click closes the modal.
  for (const el of mount.querySelectorAll<HTMLElement>(
    "[data-action='close-attachment']"
  )) {
    el.addEventListener("click", () => handlers.onAttachmentClose());
  }

  // Picker backdrop and close button.
  for (const el of mount.querySelectorAll<HTMLElement>(
    "[data-action='picker-close']"
  )) {
    el.addEventListener("click", () => handlers.onPickerClose());
  }

  // Esc key closes whichever overlay is open.
  if (args.modalOpen || args.pickerOpen) {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        document.removeEventListener("keydown", onKey);
        if (args.modalOpen) handlers.onAttachmentClose();
        else if (args.pickerOpen) handlers.onPickerClose();
      }
    };
    document.addEventListener("keydown", onKey);
  }
}

function dispatch(
  mount: HTMLElement,
  action: string | undefined,
  btn: HTMLButtonElement,
  handlers: CardHandlers
): void {
  switch (action) {
    case "confirm":
      handlers.onConfirm();
      return;
    case "edit-start":
      handlers.onEditStart();
      return;
    case "edit-cancel":
      handlers.onEditCancel();
      return;
    case "edit-submit": {
      const ta = mount.querySelector<HTMLTextAreaElement>("#correction");
      const text = (ta?.value ?? "").trim();
      if (!text) {
        ta?.focus();
        return;
      }
      handlers.onEditSubmit(text);
      return;
    }
    case "skip":
      handlers.onSkip(readNote(mount));
      return;
    case "retry":
      handlers.onRetry();
      return;
    case "open-attachment":
      handlers.onAttachmentOpen();
      return;
    case "close-attachment":
      handlers.onAttachmentClose();
      return;
    case "toggle-single": {
      const opt = btn.dataset.option ?? "";
      handlers.onSingleSelect(opt, readNote(mount));
      return;
    }
    case "toggle-multi": {
      // Toggle visual state in-place. The set of selected options is
      // re-read from the DOM at submit time, so no re-render is needed.
      const isSelected = btn.classList.toggle("selected");
      btn.setAttribute("aria-checked", String(isSelected));
      const mark = btn.querySelector<HTMLElement>(".option-mark");
      if (mark) mark.textContent = isSelected ? "✓" : "";
      return;
    }
    case "multi-submit": {
      const selected = Array.from(
        mount.querySelectorAll<HTMLButtonElement>(
          "button[data-action='toggle-multi'].selected"
        )
      ).map((b) => b.dataset.option ?? "");
      handlers.onMultiSelectSubmit(selected, readNote(mount));
      return;
    }
    case "text-submit": {
      const el = mount.querySelector<HTMLInputElement | HTMLTextAreaElement>(
        "#text-input"
      );
      const text = (el?.value ?? "").trim();
      if (!text) {
        el?.focus();
        return;
      }
      handlers.onTextSubmit(text, readNote(mount));
      return;
    }
    case "link-submit": {
      const el = mount.querySelector<HTMLInputElement>("#link-input");
      const url = (el?.value ?? "").trim();
      if (!isValidUrl(url)) {
        el?.focus();
        return;
      }
      handlers.onLinkSubmit(url, readNote(mount));
      return;
    }
    case "contact-submit": {
      const name =
        mount.querySelector<HTMLInputElement>("#c-name")?.value.trim() ?? "";
      const email =
        mount.querySelector<HTMLInputElement>("#c-email")?.value.trim() ?? "";
      const role =
        mount.querySelector<HTMLInputElement>("#c-role")?.value.trim() ?? "";
      if (!name || !email) {
        const focusEl = !name ? "#c-name" : "#c-email";
        mount.querySelector<HTMLInputElement>(focusEl)?.focus();
        return;
      }
      handlers.onContactSubmit({ name, email, role }, readNote(mount));
      return;
    }
    case "files-continue":
      handlers.onFilesContinue(readNote(mount));
      return;
    case "voice-record":
      handlers.onVoiceRecord();
      return;
    case "voice-pause":
      handlers.onVoicePause();
      return;
    case "voice-resume":
      handlers.onVoiceResume();
      return;
    case "voice-stop":
      handlers.onVoiceStop();
      return;
    case "voice-delete":
      handlers.onVoiceDelete();
      return;
    case "remove-upload": {
      const id = btn.dataset.uploadId;
      if (id) handlers.onUploadRemove(id);
      return;
    }
    case "nav-back":
      handlers.onNavBack();
      return;
    case "nav-forward":
      handlers.onNavForward();
      return;
    case "picker-open":
      handlers.onPickerOpen();
      return;
    case "picker-close":
      handlers.onPickerClose();
      return;
    case "picker-jump": {
      const i = Number(btn.dataset.index);
      if (Number.isFinite(i)) handlers.onNavJumpTo(i);
      return;
    }
  }
}

function isValidUrl(s: string): boolean {
  try {
    const u = new URL(s);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

function readNote(mount: HTMLElement): string | undefined {
  const ta = mount.querySelector<HTMLTextAreaElement>("#note-input");
  const v = (ta?.value ?? "").trim();
  return v || undefined;
}
