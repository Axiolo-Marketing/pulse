import type { Card } from "./supabase";

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
  // confirm-edit
  onConfirm: () => void;
  onEditStart: () => void;
  onEditCancel: () => void;
  onEditSubmit: (correction: string) => void;
  // typed inputs
  onSingleSelect: (option: string) => void;
  onMultiSelectSubmit: (options: string[]) => void;
  onTextSubmit: (text: string) => void;
  onLinkSubmit: (url: string) => void;
  onContactSubmit: (c: { name: string; email: string; role: string }) => void;
  // file upload
  onFilesSelected: (files: FileList) => void;
  onUploadRemove: (uploadId: string) => void;
  onFilesContinue: () => void;
  // shared
  onSkip: () => void;
  onRetry: () => void;
  // attachment modal
  onAttachmentOpen: () => void;
  onAttachmentClose: () => void;
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
  draftSelections?: Set<string>;
  showResume?: boolean;
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

  mount.innerHTML = `
    <header class="topbar">
      <span class="brand"><span class="brand-mark">IGTMS</span> · Pulse</span>
      <span class="progress">${position} of ${total}</span>
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
  `;

  attachHandlers(mount, args);
}

function renderViewBody(
  card: Card,
  mode: CardMode,
  args: RenderCardArgs
): string {
  const saving = mode === "saving";
  return `
    <p class="question">${escape(card.question)}</p>
    ${renderInput(card, saving, args)}
    <div class="actions">${renderActions(card, saving, args)}</div>
  `;
}

function renderInput(
  card: Card,
  saving: boolean,
  args: RenderCardArgs
): string {
  switch (card.response_type) {
    case "single-select":
      return renderSingleSelect(card, args.draftSelections, saving);
    case "multi-select":
      return renderMultiSelect(card, args.draftSelections, saving);
    case "short-text":
      return renderShortText(saving);
    case "long-text":
      return renderLongText(saving);
    case "document-link":
      return renderDocumentLink(saving);
    case "contact-share":
      return renderContactShare(saving);
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

function renderShortText(saving: boolean): string {
  const dis = saving ? "disabled" : "";
  return `<input id="text-input" class="input" type="text" placeholder="Your answer" ${dis} />`;
}

function renderLongText(saving: boolean): string {
  const dis = saving ? "disabled" : "";
  return `<textarea id="text-input" class="textarea" rows="5" placeholder="Your answer" ${dis}></textarea>`;
}

function renderDocumentLink(saving: boolean): string {
  const dis = saving ? "disabled" : "";
  return `<input id="link-input" class="input" type="url" inputmode="url" placeholder="https://..." ${dis} />`;
}

function renderContactShare(saving: boolean): string {
  const dis = saving ? "disabled" : "";
  return `
    <div class="contact-fields">
      <input id="c-name" class="input" type="text" placeholder="Name" ${dis} />
      <input id="c-email" class="input" type="email" inputmode="email" placeholder="Email" ${dis} />
      <input id="c-role" class="input" type="text" placeholder="Role" ${dis} />
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
  const placeholder = "What should we update? A short note is fine.";
  const prefill = card.default_value ?? "";
  return `
    <p class="question">${escape(card.question)}</p>
    <textarea
      id="correction"
      class="textarea"
      rows="5"
      placeholder="${escape(placeholder)}"
      autofocus
    >${escape(prefill)}</textarea>
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

  // Esc key closes the modal when it's open.
  if (args.modalOpen) {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        document.removeEventListener("keydown", onKey);
        handlers.onAttachmentClose();
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
      handlers.onSkip();
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
      handlers.onSingleSelect(opt);
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
      handlers.onMultiSelectSubmit(selected);
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
      handlers.onTextSubmit(text);
      return;
    }
    case "link-submit": {
      const el = mount.querySelector<HTMLInputElement>("#link-input");
      const url = (el?.value ?? "").trim();
      if (!isValidUrl(url)) {
        el?.focus();
        return;
      }
      handlers.onLinkSubmit(url);
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
      handlers.onContactSubmit({ name, email, role });
      return;
    }
    case "files-continue":
      handlers.onFilesContinue();
      return;
    case "remove-upload": {
      const id = btn.dataset.uploadId;
      if (id) handlers.onUploadRemove(id);
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
