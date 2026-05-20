import type { Card, ClientResponse } from "./api";

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

export function renderComplete(mount: HTMLElement, name: string): void {
  mount.innerHTML = `
    <div class="card complete" role="status">
      <div class="category">Thank you</div>
      <h1 class="card-title">All done, ${escape(firstName(name))}</h1>
      <hr class="divider" />
      <p class="context">
        Your responses are with Tom. He will follow up directly.
      </p>
    </div>
  `;
}

function firstName(full: string): string {
  return full.split(" ")[0] ?? full;
}

export type CardMode = "view" | "edit" | "saving";

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
  modalOpen: boolean;
  pickerOpen?: boolean;
  draftSelections?: Set<string>;
  showResume?: boolean;
  existingResponse?: ClientResponse;
  cards?: Card[];
  responses?: Map<string, ClientResponse>;
  handlers: CardHandlers;
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
    handlers,
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

  // baseUrl may or may not end with `/` depending on Astro's `base` config.
  const baseSlash = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;

  mount.innerHTML = `
    <header class="topbar">
      <span class="brand">
        <img src="${baseSlash}axiolo-logo.svg" alt="Axiolo" class="brand-logo" width="84" height="23" />
        <span class="brand-sep" aria-hidden="true">·</span>
        Pulse
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
    ${renderNoteField(card, saving, args.existingResponse)}
    <div class="actions">${renderActions(card, saving, args)}</div>
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
      const continueDisabled = saving || hasPending || !hasFiles;
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

function renderModal(card: Card, baseUrl: string): string {
  const path = card.attachment_path ?? "";
  // baseUrl from Astro ends with a slash. attachment_path is a relative
  // path like 'deliverables/glc-org-chart.html'. Compose as one URL.
  const src = baseUrl.endsWith("/") ? baseUrl + path : `${baseUrl}/${path}`;
  return `
    <div class="modal" role="dialog" aria-modal="true" aria-label="${escapeAttr(card.title)}">
      <div class="modal-backdrop" data-action="close-attachment"></div>
      <div class="modal-panel">
        <header class="modal-header">
          <span class="modal-title">${escape(card.title)} reference</span>
          <button class="modal-close" type="button" data-action="close-attachment" aria-label="Close">×</button>
        </header>
        <iframe
          class="modal-iframe"
          src="${escapeAttr(src)}"
          sandbox="allow-scripts"
          title="${escapeAttr(card.title)} reference"
          loading="lazy"
        ></iframe>
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
      const opt = btn.dataset.option ?? "";
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
