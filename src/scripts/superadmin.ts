// Superadmin page — cross-tenant org list + create-org form.
//
// Rendered at `/admin/#superadmin` and only reachable when the
// caller's `AuthUser.is_superadmin === true`. The page is hidden in
// the nav for non-superadmin operators, but the backend
// (`get_current_superadmin`) is still the authority — the frontend
// just keeps non-super users from seeing a route they couldn't use.
//
// Mirrors the `settings.ts` shape: a single `renderSuperadmin(args)`
// entry point that paints HTML strings and binds handlers in-place.
// No framework, no shadow DOM.

import {
  ApiError,
  superadminApi,
  type AuthUser,
  type SuperadminMemberRow,
  type SuperadminOrgRow,
} from "../lib/api";
import { formatTimestamp } from "../lib/format-time";

export interface SuperadminHostHelpers {
  /** Brief transient toast. */
  toast: (msg: string) => void;
}

export interface RenderSuperadminArgs {
  container: HTMLElement;
  user: AuthUser;
  helpers: SuperadminHostHelpers;
}

const escape = (s: string): string =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

// Same email-shape regex as settings.ts — keeps validation centralized
// in vibe rather than imports. Backend is the source of truth; this is
// just to skip an obvious 422 round-trip.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// Slug shape mirrors `_SLUG_RE` in `api/pulse_api/routes/superadmin.py`.
// Lower-case alphanumerics with single internal hyphens, 2-40 chars.
const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const SLUG_MIN = 2;
const SLUG_MAX = 40;

// ── Top-level entry point ─────────────────────────────────────────────────

export async function renderSuperadmin(args: RenderSuperadminArgs): Promise<void> {
  const { container, user, helpers } = args;

  // Defense-in-depth: if a non-superadmin sneaks past the nav gate
  // (URL hash bookmark, browser back-button), show a friendly empty
  // state instead of making four 403'd calls.
  if (!user.is_superadmin) {
    container.innerHTML = renderNotFound();
    return;
  }

  container.innerHTML = renderShell();
  // Render the list lazily so a slow superadmin-orgs query doesn't
  // block the rest of the page from showing the create form.
  const listEl = container.querySelector<HTMLElement>("#superadmin-org-list");
  if (listEl) {
    listEl.innerHTML = `<p class="superadmin-loading">Loading organizations…</p>`;
  }

  bindCreateForm(container, helpers);

  await refreshList(container, helpers);
}

function renderShell(): string {
  return `
    <section class="superadmin-page">
      <header class="superadmin-head">
        <h2 class="superadmin-h">Superadmin</h2>
        <p class="superadmin-sub">Tools across all organizations.</p>
      </header>

      <section class="settings-section" aria-labelledby="superadmin-create-h">
        <h3 class="settings-section-h" id="superadmin-create-h">Create organization</h3>
        <p class="settings-section-p">
          We'll set up the org and email the owner a link to claim it.
          The link expires in 7 days.
        </p>
        <form class="settings-form superadmin-create-form" id="superadmin-create-form" novalidate>
          <label class="edit-field">
            <span class="edit-label">Organization name</span>
            <input id="superadmin-name" class="input" type="text" maxlength="200"
                   placeholder="e.g. Acme" autocomplete="off" required />
          </label>
          <label class="edit-field">
            <span class="edit-label">Slug</span>
            <input id="superadmin-slug" class="input" type="text"
                   placeholder="acme" autocomplete="off"
                   minlength="${SLUG_MIN}" maxlength="${SLUG_MAX}" required />
            <span class="settings-section-p" style="margin:6px 0 0">
              Lower-case letters, digits, and hyphens. Lives in URLs forever.
            </span>
          </label>
          <label class="edit-field">
            <span class="edit-label">Owner email</span>
            <input id="superadmin-owner" class="input" type="email"
                   placeholder="founder@acme.example" autocomplete="off" required />
          </label>
          <div class="settings-form-actions">
            <button class="btn btn-primary" type="submit">Send invite</button>
            <span class="settings-form-msg" id="superadmin-create-msg" role="status" aria-live="polite"></span>
          </div>
        </form>
      </section>

      <section class="settings-section" aria-labelledby="superadmin-list-h">
        <h3 class="settings-section-h" id="superadmin-list-h">All organizations</h3>
        <div id="superadmin-org-list" class="superadmin-list-slot"></div>
      </section>
    </section>
  `;
}

function renderNotFound(): string {
  return `
    <section class="superadmin-empty" role="status">
      <h2 class="superadmin-h">Nothing here</h2>
      <p class="settings-section-p">
        This page is for cross-organization admins. Your account doesn't have access.
      </p>
      <a class="btn btn-primary" href="#" data-go-home>Back to engagements</a>
    </section>
  `;
}

// ── List rendering ────────────────────────────────────────────────────────

async function refreshList(
  container: HTMLElement,
  helpers: SuperadminHostHelpers,
): Promise<void> {
  const listEl = container.querySelector<HTMLElement>("#superadmin-org-list");
  if (!listEl) return;
  try {
    const rows = await superadminApi.listOrgs({ limit: 100 });
    paintList(listEl, rows, helpers, container);
  } catch (err) {
    const detail = err instanceof ApiError ? err.detail : "Could not load";
    listEl.innerHTML = `<p class="superadmin-error">${escape(detail)}</p>`;
  }
}

function paintList(
  listEl: HTMLElement,
  rows: SuperadminOrgRow[],
  helpers: SuperadminHostHelpers,
  container: HTMLElement,
): void {
  if (rows.length === 0) {
    // Axiolo always exists, so this is a "no matches" copy reserved
    // for the filter feature we'll add later.
    listEl.innerHTML = `
      <p class="superadmin-empty-list">
        No organizations match this view.
      </p>
    `;
    return;
  }

  // Desktop table + mobile card list share the same data. CSS toggles
  // which one is visible at the 768px breakpoint.
  listEl.innerHTML = `
    <div class="superadmin-table-wrap" role="region" aria-label="Organizations">
      <table class="superadmin-table">
        <thead>
          <tr>
            <th scope="col">Organization</th>
            <th scope="col" class="num">Members</th>
            <th scope="col" class="num">Pending invites</th>
            <th scope="col">Owners</th>
            <th scope="col">Created</th>
            <th scope="col" aria-label="Actions"></th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(rowMarkup).join("")}
        </tbody>
      </table>
    </div>
    <ul class="superadmin-cards" role="list">
      ${rows.map(cardMarkup).join("")}
    </ul>
  `;

  bindRowActions(listEl, rows, helpers, container);
}

function rowMarkup(row: SuperadminOrgRow): string {
  const owners = row.owner_emails.length
    ? row.owner_emails.map(escape).join(", ")
    : `<span class="muted">—</span>`;
  const canDelete = row.member_count <= 1;
  return `
    <tr class="superadmin-row"
        data-org-id="${escape(row.id)}"
        data-org-slug="${escape(row.slug)}"
        data-org-name="${escape(row.name)}"
        data-can-delete="${canDelete ? "1" : "0"}">
      <th scope="row">
        <div class="superadmin-org-name">${escape(row.name)}</div>
        <div class="superadmin-org-slug">${escape(row.slug)}</div>
      </th>
      <td class="num">${row.member_count}</td>
      <td class="num">${row.pending_invite_count}</td>
      <td class="superadmin-owners">${owners}</td>
      <td class="superadmin-when">${escape(formatTimestamp(row.created_at))}</td>
      <td class="superadmin-actions-cell">
        <button class="btn-ghost-sm" type="button" data-action="view-members">View members</button>
        ${
          canDelete
            ? `<button class="btn-ghost-sm danger" type="button" data-action="delete-org">Delete</button>`
            : `<button class="btn-ghost-sm" type="button" disabled
                       aria-label="Delete disabled — org has members"
                       title="Remove members before deleting">Delete</button>`
        }
        <div class="superadmin-row-confirm" role="alertdialog" aria-modal="false" hidden></div>
      </td>
    </tr>
  `;
}

function cardMarkup(row: SuperadminOrgRow): string {
  const owners = row.owner_emails.length
    ? row.owner_emails.map(escape).join(", ")
    : `<span class="muted">No owners</span>`;
  const canDelete = row.member_count <= 1;
  return `
    <li class="superadmin-card"
        data-org-id="${escape(row.id)}"
        data-org-name="${escape(row.name)}"
        data-can-delete="${canDelete ? "1" : "0"}">
      <header class="superadmin-card-head">
        <span class="superadmin-card-name">${escape(row.name)}</span>
        <span class="superadmin-card-slug">${escape(row.slug)}</span>
      </header>
      <dl class="superadmin-card-stats">
        <div><dt>Members</dt><dd>${row.member_count}</dd></div>
        <div><dt>Pending</dt><dd>${row.pending_invite_count}</dd></div>
        <div><dt>Created</dt><dd>${escape(formatTimestamp(row.created_at))}</dd></div>
      </dl>
      <p class="superadmin-card-owners">${owners}</p>
      <div class="superadmin-card-actions">
        <button class="btn-ghost-sm" type="button" data-action="view-members">View members</button>
        ${
          canDelete
            ? `<button class="btn-ghost-sm danger" type="button" data-action="delete-org">Delete</button>`
            : `<button class="btn-ghost-sm" type="button" disabled
                       title="Remove members before deleting">Delete</button>`
        }
      </div>
      <div class="superadmin-row-confirm" role="alertdialog" aria-modal="false" hidden></div>
    </li>
  `;
}

// ── Handlers ──────────────────────────────────────────────────────────────

function bindCreateForm(
  container: HTMLElement,
  helpers: SuperadminHostHelpers,
): void {
  const form = container.querySelector<HTMLFormElement>("#superadmin-create-form");
  const msg = container.querySelector<HTMLElement>("#superadmin-create-msg");
  if (!form || !msg) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.textContent = "";
    msg.classList.remove("error", "success");

    const nameInput = form.querySelector<HTMLInputElement>("#superadmin-name");
    const slugInput = form.querySelector<HTMLInputElement>("#superadmin-slug");
    const ownerInput = form.querySelector<HTMLInputElement>("#superadmin-owner");
    const name = (nameInput?.value ?? "").trim();
    const slug = (slugInput?.value ?? "").trim().toLowerCase();
    const owner = (ownerInput?.value ?? "").trim();

    const showError = (text: string, focusEl?: HTMLInputElement | null): void => {
      msg.textContent = text;
      msg.classList.add("error");
      focusEl?.focus();
    };

    if (!name) return showError("Name is required.", nameInput);
    if (!slug) return showError("Slug is required.", slugInput);
    if (slug.length < SLUG_MIN || slug.length > SLUG_MAX) {
      return showError(
        `Slug must be ${SLUG_MIN}-${SLUG_MAX} characters.`,
        slugInput,
      );
    }
    if (!SLUG_RE.test(slug)) {
      return showError(
        "Slug must be lower-case letters, digits, and hyphens.",
        slugInput,
      );
    }
    if (!EMAIL_RE.test(owner)) {
      return showError("Enter a valid owner email.", ownerInput);
    }

    const submitBtn = form.querySelector<HTMLButtonElement>("button[type='submit']");
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Sending…";
    }
    try {
      const result = await superadminApi.createOrg({
        name,
        slug,
        owner_email: owner,
      });
      msg.textContent = `Invite sent to ${result.invite.email}`;
      msg.classList.add("success");
      // Reset the form so a second org is one form away.
      if (nameInput) nameInput.value = "";
      if (slugInput) slugInput.value = "";
      if (ownerInput) ownerInput.value = "";
      helpers.toast(`Created ${result.org.name}`);
      await refreshList(container, helpers);
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : "Could not create";
      showError(detail);
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Send invite";
      }
    }
  });
}

function bindRowActions(
  listEl: HTMLElement,
  rows: SuperadminOrgRow[],
  helpers: SuperadminHostHelpers,
  container: HTMLElement,
): void {
  listEl.addEventListener("click", (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn || btn.disabled) return;

    const rowEl = btn.closest<HTMLElement>(
      ".superadmin-row, .superadmin-card",
    );
    if (!rowEl) return;
    const orgId = rowEl.dataset.orgId;
    const orgName = rowEl.dataset.orgName ?? "this organization";
    if (!orgId) return;

    if (btn.dataset.action === "view-members") {
      void openMembersDrawer({ orgId, orgName, helpers });
      return;
    }

    if (btn.dataset.action === "delete-org") {
      const confirmSlot = rowEl.querySelector<HTMLElement>(
        ".superadmin-row-confirm",
      );
      if (!confirmSlot) return;
      showInlineConfirm(confirmSlot, {
        message: `Delete ${orgName}? This cannot be undone.`,
        confirmLabel: "Delete",
        danger: true,
        onConfirm: async () => {
          try {
            await superadminApi.deleteOrg(orgId);
            helpers.toast(`${orgName} deleted`);
            await refreshList(container, helpers);
          } catch (err) {
            const detail =
              err instanceof ApiError ? err.detail : "Could not delete";
            helpers.toast(detail);
          }
        },
      });
    }
  });
  void rows;
}

// ── Members drawer (read-only) ────────────────────────────────────────────

async function openMembersDrawer(opts: {
  orgId: string;
  orgName: string;
  helpers: SuperadminHostHelpers;
}): Promise<void> {
  // Reentrancy guard.
  document.body
    .querySelectorAll<HTMLElement>(".modal.superadmin-members-modal")
    .forEach((el) => el.remove());

  const modalEl = document.createElement("div");
  modalEl.className = "modal superadmin-members-modal";
  modalEl.innerHTML = `
    <div class="modal-backdrop" data-close></div>
    <div class="modal-panel superadmin-members-panel" role="dialog"
         aria-modal="true" aria-labelledby="superadmin-members-title">
      <header class="modal-header">
        <span class="modal-title" id="superadmin-members-title">
          Members of ${escape(opts.orgName)}
        </span>
        <button class="modal-close" type="button" data-close aria-label="Close">×</button>
      </header>
      <div class="superadmin-members-body" id="superadmin-members-body">
        <p class="superadmin-loading">Loading members…</p>
      </div>
    </div>
  `;
  document.body.appendChild(modalEl);

  const close = (): void => {
    modalEl.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (e: KeyboardEvent): void => {
    if (e.key === "Escape") close();
  };
  document.addEventListener("keydown", onKey);
  for (const el of modalEl.querySelectorAll<HTMLElement>("[data-close]")) {
    el.addEventListener("click", close);
  }

  const body = modalEl.querySelector<HTMLElement>("#superadmin-members-body");
  if (!body) return;
  try {
    const members = await superadminApi.listOrgMembers(opts.orgId);
    body.innerHTML = renderMembersDrawerBody(members);
  } catch (err) {
    const detail = err instanceof ApiError ? err.detail : "Could not load";
    body.innerHTML = `<p class="superadmin-error">${escape(detail)}</p>`;
  }
}

function renderMembersDrawerBody(members: SuperadminMemberRow[]): string {
  if (members.length === 0) {
    return `<p class="superadmin-empty-list">No members yet.</p>`;
  }
  return `
    <ul class="superadmin-members-list" role="list">
      ${members
        .map((m) => {
          const display = (m.name && m.name.trim()) || m.email;
          return `
            <li class="superadmin-member">
              <div class="superadmin-member-identity">
                <span class="superadmin-member-name">${escape(display)}</span>
                <span class="superadmin-member-email">${escape(m.email)}</span>
              </div>
              <span class="superadmin-member-role superadmin-member-role--${escape(m.role)}">
                ${escape(m.role)}
              </span>
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

// ── Inline-confirm (matches the settings.ts pattern) ──────────────────────

interface InlineConfirmOpts {
  message: string;
  confirmLabel: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void | Promise<void>;
}

function showInlineConfirm(
  slot: HTMLElement,
  opts: InlineConfirmOpts,
): void {
  slot.hidden = false;
  slot.innerHTML = `
    <span class="inline-confirm-msg">${escape(opts.message)}</span>
    <div class="inline-confirm-actions">
      <button class="btn-ghost-sm" type="button" data-inline="cancel">${escape(opts.cancelLabel ?? "Cancel")}</button>
      <button class="${opts.danger ? "btn-danger-sm" : "btn-primary-sm"}" type="button" data-inline="confirm">${escape(opts.confirmLabel)}</button>
    </div>
  `;
  const cancelBtn = slot.querySelector<HTMLButtonElement>(
    "[data-inline='cancel']",
  );
  const confirmBtn = slot.querySelector<HTMLButtonElement>(
    "[data-inline='confirm']",
  );

  const dismiss = (): void => {
    slot.hidden = true;
    slot.innerHTML = "";
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (e: KeyboardEvent): void => {
    if (e.key === "Escape") {
      e.preventDefault();
      dismiss();
    }
  };
  document.addEventListener("keydown", onKey);

  cancelBtn?.addEventListener("click", dismiss);
  confirmBtn?.addEventListener("click", async () => {
    if (!confirmBtn) return;
    confirmBtn.disabled = true;
    const original = confirmBtn.textContent;
    confirmBtn.textContent = "Working…";
    try {
      await opts.onConfirm();
      // The caller refreshes the list; this slot is replaced.
      document.removeEventListener("keydown", onKey);
    } catch (err) {
      console.error("inline confirm action:", err);
      confirmBtn.disabled = false;
      confirmBtn.textContent = original;
    }
  });
  cancelBtn?.focus();
}
