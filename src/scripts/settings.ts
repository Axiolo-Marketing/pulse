// Settings page — Personal tab + Organization tab.
//
// Two tabs live under the same container; the URL hash drives which one
// is active (`#settings/personal` and `#settings/organization`). The
// Organization tab renders different controls based on the caller's role
// (owner vs member) — owners see the full surface, members see a
// read-only org name + member list with the inline "Only owners can
// change org settings." caption.
//
// This module renders HTML strings and binds handlers in-place — same
// pattern as the existing admin.ts. No framework, no shadow DOM.
import {
  ApiError,
  authApi,
  type ApiKeySummary,
  type ApiKeyWithSecret,
  type AuthUser,
  type InviteSummary,
  type MemberRow,
  type OAuthIdentitySummary,
  type OrgDetails,
  orgsApi,
  orgLogoUrl,
} from "../lib/api";
import { formatTimestamp } from "../lib/format-time";

export type SettingsTab = "personal" | "organization";

export interface SettingsHostHelpers {
  /** Brief transient toast. */
  toast: (msg: string) => void;
  /** Confirm via modal — same shape as the existing openConfirmModal. */
  confirm: (opts: {
    title: string;
    body: string;
    confirmLabel: string;
    cancelLabel?: string;
    danger?: boolean;
    onConfirm: () => void | Promise<void>;
  }) => void;
  /** Called after the org metadata (name / logo) changes so the parent
   * shell can refresh the switcher and any other org-bound chrome. */
  onOrgChanged: () => Promise<void> | void;
}

const escape = (s: string): string =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

const PROVIDER_LABELS: Record<string, string> = {
  google: "Google",
  microsoft: "Microsoft 365",
};

// Whitespace-tolerant email check — the backend re-validates so this is
// purely about not bothering the network on obvious typos.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function flashCopied(btn: HTMLButtonElement, label: string): void {
  const original = btn.textContent ?? "";
  btn.textContent = label;
  btn.classList.add("copied-flash");
  setTimeout(() => {
    btn.textContent = original;
    btn.classList.remove("copied-flash");
  }, 1200);
}

// ── Tab chrome ────────────────────────────────────────────────────────────

export interface RenderSettingsArgs {
  container: HTMLElement;
  tab: SettingsTab;
  user: AuthUser;
  org: OrgDetails;
  identities: OAuthIdentitySummary[];
  apiKeys: ApiKeySummary[];
  members: MemberRow[];
  invites: InviteSummary[];
  helpers: SettingsHostHelpers;
}

export function renderSettings(args: RenderSettingsArgs): void {
  const { container, tab } = args;

  container.innerHTML = `
    <div class="settings-page">
      <header class="settings-page-head">
        <h2 class="settings-h">Settings</h2>
        <nav class="settings-tabs" role="tablist" aria-label="Settings sections">
          <a
            class="settings-tab${tab === "personal" ? " is-active" : ""}"
            role="tab"
            aria-selected="${tab === "personal" ? "true" : "false"}"
            aria-controls="settings-tabpanel"
            href="#settings/personal"
            id="settings-tab-personal"
          >Personal</a>
          <a
            class="settings-tab${tab === "organization" ? " is-active" : ""}"
            role="tab"
            aria-selected="${tab === "organization" ? "true" : "false"}"
            aria-controls="settings-tabpanel"
            href="#settings/organization"
            id="settings-tab-organization"
          >Organization</a>
        </nav>
      </header>
      <div
        class="settings-tabpanel"
        role="tabpanel"
        aria-labelledby="settings-tab-${tab}"
        id="settings-tabpanel"
      ></div>
    </div>
  `;

  const panel = container.querySelector<HTMLElement>("#settings-tabpanel");
  if (!panel) return;

  if (tab === "personal") {
    renderPersonalTab(panel, args);
  } else {
    renderOrgTab(panel, args);
  }
}

// ── Personal tab ──────────────────────────────────────────────────────────

function renderPersonalTab(
  panel: HTMLElement,
  args: RenderSettingsArgs,
): void {
  const { user, identities, apiKeys, helpers } = args;
  const hasPw = user.has_password;
  const pwTitle = hasPw ? "Change password" : "Set a password";
  const pwIntro = hasPw
    ? "Update the password you use to sign in."
    : "You signed in with a third-party provider. Set a password to enable email/password sign-in (useful for CLI or API access).";

  const identityRows = identities.length
    ? identities
        .map(
          (i) => `
        <li class="settings-identity">
          <span class="settings-identity-name">${escape(PROVIDER_LABELS[i.provider] ?? i.provider)}</span>
          <span class="settings-identity-when">linked ${escape(formatTimestamp(i.linked_at))}</span>
        </li>`,
        )
        .join("")
    : `<li class="settings-identity empty">No third-party accounts linked.</li>`;

  panel.innerHTML = `
    <section class="settings-section" aria-labelledby="profile-h">
      <h3 class="settings-section-h" id="profile-h">Profile</h3>
      <form class="settings-form" id="profile-form" novalidate>
        <label class="edit-field">
          <span class="edit-label">Email</span>
          <input class="input" type="email" value="${escape(user.email)}" disabled />
        </label>
        <label class="edit-field">
          <span class="edit-label">Display name</span>
          <input id="profile-name" class="input" type="text" autocomplete="name"
                 value="${escape(user.name ?? "")}" />
        </label>
        <div class="settings-form-actions">
          <button class="btn btn-primary" type="submit">Save profile</button>
          <span class="settings-form-msg" id="profile-msg" role="status" aria-live="polite"></span>
        </div>
      </form>
    </section>

    <section class="settings-section" aria-labelledby="pw-h">
      <h3 class="settings-section-h" id="pw-h">${escape(pwTitle)}</h3>
      <p class="settings-section-p">${escape(pwIntro)}</p>
      <form class="settings-form" id="password-form" novalidate>
        ${
          hasPw
            ? `<label class="edit-field">
                 <span class="edit-label">Current password</span>
                 <input id="pw-current" class="input" type="password"
                        autocomplete="current-password" required />
               </label>`
            : ""
        }
        <label class="edit-field">
          <span class="edit-label">New password (8+ characters)</span>
          <input id="pw-new" class="input" type="password"
                 autocomplete="new-password" minlength="8" required />
        </label>
        <label class="edit-field">
          <span class="edit-label">Confirm new password</span>
          <input id="pw-confirm" class="input" type="password"
                 autocomplete="new-password" minlength="8" required />
        </label>
        <div class="settings-form-actions">
          <button class="btn btn-primary" type="submit">${escape(hasPw ? "Update password" : "Set password")}</button>
          <span class="settings-form-msg" id="password-msg" role="status" aria-live="polite"></span>
        </div>
      </form>
    </section>

    <section class="settings-section" aria-labelledby="linked-h">
      <h3 class="settings-section-h" id="linked-h">Linked accounts</h3>
      <ul class="settings-identity-list">${identityRows}</ul>
    </section>

    <section class="settings-section" id="api-keys-section" aria-labelledby="apikeys-h">
      <h3 class="settings-section-h" id="apikeys-h">API keys</h3>
      <p class="settings-section-p">Use these to authenticate non-browser clients — CLI scripts, CI, Claude / MCP integrations. Each key is scoped to the org it was created in.</p>
      <div id="api-keys-list" class="api-key-list">${renderApiKeyRows(apiKeys)}</div>
      <div class="api-keys-actions">
        <button class="btn-primary-sm" type="button" id="create-api-key">+ Create new key</button>
      </div>
    </section>
  `;

  bindPersonalHandlers(panel, args);
  bindApiKeyRowHandlers(panel, apiKeys, helpers);
}

function bindPersonalHandlers(
  panel: HTMLElement,
  args: RenderSettingsArgs,
): void {
  const { user, helpers } = args;
  const hasPw = user.has_password;

  // ── Profile form ──
  const profileForm = panel.querySelector<HTMLFormElement>("#profile-form");
  const profileMsg = panel.querySelector<HTMLElement>("#profile-msg");
  profileForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!profileMsg) return;
    profileMsg.textContent = "";
    profileMsg.classList.remove("error", "success");
    const nameInput = panel.querySelector<HTMLInputElement>("#profile-name");
    const nameVal = (nameInput?.value ?? "").trim();
    try {
      const updated = await authApi.updateProfile({ name: nameVal || null });
      profileMsg.textContent = "Saved";
      profileMsg.classList.add("success");
      if (nameInput) nameInput.value = updated.name ?? "";
    } catch (err) {
      profileMsg.textContent =
        err instanceof ApiError ? err.detail : "Could not save";
      profileMsg.classList.add("error");
    }
  });

  // ── Password form ──
  const pwForm = panel.querySelector<HTMLFormElement>("#password-form");
  const pwMsg = panel.querySelector<HTMLElement>("#password-msg");
  pwForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!pwMsg) return;
    pwMsg.textContent = "";
    pwMsg.classList.remove("error", "success");
    const newPw = panel.querySelector<HTMLInputElement>("#pw-new")?.value ?? "";
    const confirmPw =
      panel.querySelector<HTMLInputElement>("#pw-confirm")?.value ?? "";
    const currentPw = hasPw
      ? panel.querySelector<HTMLInputElement>("#pw-current")?.value ?? ""
      : null;

    if (newPw.length < 8) {
      pwMsg.textContent = "Password must be at least 8 characters.";
      pwMsg.classList.add("error");
      return;
    }
    if (newPw !== confirmPw) {
      pwMsg.textContent = "Passwords do not match.";
      pwMsg.classList.add("error");
      return;
    }

    try {
      await authApi.changePassword({
        current_password: currentPw,
        new_password: newPw,
      });
      helpers.toast("Password updated");
      // Re-render this tab so the form switches between
      // "Set password" / "Change password" mode and the inputs clear.
      const refreshed = await authApi.me();
      renderSettings({ ...args, user: refreshed, tab: "personal" });
    } catch (err) {
      pwMsg.textContent =
        err instanceof ApiError ? err.detail : "Could not update password";
      pwMsg.classList.add("error");
    }
  });

  // ── API keys ──
  panel
    .querySelector<HTMLButtonElement>("#create-api-key")
    ?.addEventListener("click", () => {
      openCreateApiKeyModal(args);
    });
}

// ── API keys helpers (moved from admin.ts so they live with the
//     Personal tab) ────────────────────────────────────────────────────────

function renderApiKeyRows(keys: ApiKeySummary[]): string {
  if (keys.length === 0) {
    return `<p class="api-key-empty">No API keys yet.</p>`;
  }
  return keys
    .map(
      (k) => `
      <div class="api-key-row" data-key-id="${escape(k.id)}" data-key-label="${escape(k.label)}">
        <div class="api-key-info">
          <div class="api-key-label">${escape(k.label)}</div>
          <div class="api-key-prefix">pulse_${escape(k.prefix)}…</div>
          <div class="api-key-meta">
            <span>Last used ${escape(k.last_used_at ? formatTimestamp(k.last_used_at) : "Never used")}</span>
            <span class="api-key-meta-sep">·</span>
            <span>Created ${escape(formatTimestamp(k.created_at))}</span>
          </div>
        </div>
        <button class="btn-ghost-sm danger" type="button" data-action="revoke-api-key">Revoke</button>
      </div>`,
    )
    .join("");
}

function bindApiKeyRowHandlers(
  scope: HTMLElement,
  keys: ApiKeySummary[],
  helpers: SettingsHostHelpers,
): void {
  for (const row of scope.querySelectorAll<HTMLElement>(".api-key-row")) {
    const btn = row.querySelector<HTMLButtonElement>(
      "[data-action='revoke-api-key']",
    );
    if (!btn) continue;
    btn.addEventListener("click", () => {
      const id = row.dataset.keyId!;
      const label = row.dataset.keyLabel ?? "this key";
      helpers.confirm({
        title: "Revoke API key",
        body: `Revoke '${label}'? Any client using this key will stop working immediately. This cannot be undone.`,
        confirmLabel: "Revoke",
        danger: true,
        onConfirm: async () => {
          await authApi.revokeApiKey(id);
          helpers.toast("API key revoked");
          // Refresh the list in place so the row vanishes without a
          // full re-render of the whole tab.
          const fresh = await authApi.listApiKeys();
          keys.splice(0, keys.length, ...fresh);
          const listEl = scope.querySelector<HTMLElement>("#api-keys-list");
          if (listEl) {
            listEl.innerHTML = renderApiKeyRows(fresh);
            bindApiKeyRowHandlers(scope, keys, helpers);
          }
        },
      });
    });
  }
}

function openCreateApiKeyModal(args: RenderSettingsArgs): void {
  if (document.body.querySelector(".modal.create-api-key-modal")) return;

  const modalEl = document.createElement("div");
  modalEl.className = "modal create-api-key-modal";
  modalEl.innerHTML = `
    <div class="modal-backdrop" data-close></div>
    <div class="modal-panel confirm-panel" role="dialog" aria-modal="true" aria-labelledby="apikey-modal-title">
      <header class="modal-header">
        <span class="modal-title" id="apikey-modal-title">Create API key</span>
        <button class="modal-close" type="button" data-close aria-label="Close">×</button>
      </header>
      <div class="confirm-body" id="create-api-key-body">
        <form class="settings-form" id="create-api-key-form" novalidate style="max-width:none">
          <label class="edit-field">
            <span class="edit-label">Label</span>
            <input class="input" id="api-key-label-input" type="text"
                   placeholder="e.g. MCP — Claude Code" maxlength="100" autofocus required />
            <span class="settings-section-p" style="margin:6px 0 0">So you remember what this key is for.</span>
          </label>
          <p class="settings-section-p" style="margin:0">Will be created in <strong>${escape(args.org.name)}</strong>.</p>
          <span class="settings-form-msg" id="api-key-form-msg" role="status" aria-live="polite"></span>
        </form>
      </div>
      <div class="confirm-actions" id="create-api-key-actions">
        <button class="btn-ghost-sm" type="button" data-close>Cancel</button>
        <button class="btn-primary-sm" type="button" id="api-key-submit">Create</button>
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

  const labelInput = modalEl.querySelector<HTMLInputElement>(
    "#api-key-label-input",
  )!;
  const msgEl = modalEl.querySelector<HTMLElement>("#api-key-form-msg")!;
  const submitBtn = modalEl.querySelector<HTMLButtonElement>(
    "#api-key-submit",
  )!;

  const showError = (msg: string): void => {
    msgEl.textContent = msg;
    msgEl.classList.remove("success");
    msgEl.classList.add("error");
  };
  const clearError = (): void => {
    msgEl.textContent = "";
    msgEl.classList.remove("error", "success");
  };
  labelInput.addEventListener("input", clearError);

  const submit = async (): Promise<void> => {
    clearError();
    const label = labelInput.value.trim();
    if (!label) {
      showError("Label is required.");
      labelInput.focus();
      return;
    }
    submitBtn.disabled = true;
    const originalText = submitBtn.textContent;
    submitBtn.textContent = "Creating...";
    try {
      const created = await authApi.createApiKey({ label });
      showReveal(created);
      // Refresh the apiKeys list so the row shows up after the modal
      // closes. The host re-renders the section via the existing
      // bindApiKeyRowHandlers loop.
      const fresh = await authApi.listApiKeys();
      args.apiKeys.splice(0, args.apiKeys.length, ...fresh);
      const listEl = document.querySelector<HTMLElement>("#api-keys-list");
      if (listEl) {
        listEl.innerHTML = renderApiKeyRows(fresh);
        const section = listEl.closest<HTMLElement>("#api-keys-section");
        if (section) bindApiKeyRowHandlers(section, args.apiKeys, args.helpers);
      }
    } catch (err) {
      const detail =
        err instanceof ApiError ? err.detail : "Could not create API key";
      showError(detail);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = originalText;
    }
  };

  submitBtn.addEventListener("click", () => void submit());
  modalEl
    .querySelector<HTMLFormElement>("#create-api-key-form")
    ?.addEventListener("submit", (e) => {
      e.preventDefault();
      void submit();
    });

  function showReveal(created: ApiKeyWithSecret): void {
    const bodyEl = modalEl.querySelector<HTMLElement>("#create-api-key-body")!;
    const actionsEl = modalEl.querySelector<HTMLElement>(
      "#create-api-key-actions",
    )!;
    const titleEl = modalEl.querySelector<HTMLElement>(".modal-title")!;
    titleEl.textContent = "Key created — copy it now";

    bodyEl.innerHTML = `
      <div class="api-key-reveal">
        <div class="api-key-warning">This is the only time you'll see the full key. Store it somewhere safe.</div>
        <div class="api-key-reveal-row">
          <input class="input api-key-reveal-input" id="api-key-reveal-input" type="text" readonly value="${escape(created.key)}" />
          <button class="btn-secondary-sm" type="button" id="api-key-copy" aria-label="Copy API key">Copy</button>
        </div>
      </div>
    `;
    actionsEl.innerHTML = `
      <button class="btn-primary-sm" type="button" id="api-key-done">Done</button>
    `;

    const revealInput = modalEl.querySelector<HTMLInputElement>(
      "#api-key-reveal-input",
    )!;
    revealInput.addEventListener("focus", () => revealInput.select());
    revealInput.focus();

    const copyBtn = modalEl.querySelector<HTMLButtonElement>("#api-key-copy")!;
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(created.key);
        flashCopied(copyBtn, "Copied!");
      } catch (err) {
        console.error("copy api key:", err);
        args.helpers.toast(
          "Could not copy — select the field and copy manually.",
        );
      }
    });

    modalEl
      .querySelector<HTMLButtonElement>("#api-key-done")
      ?.addEventListener("click", close);
  }
}

// ── Organization tab ──────────────────────────────────────────────────────

function renderOrgTab(panel: HTMLElement, args: RenderSettingsArgs): void {
  const { org } = args;
  const isOwner = org.role === "owner";

  panel.innerHTML = `
    <section class="settings-section settings-section--org-header" aria-labelledby="org-h">
      <div class="org-header">
        <div class="org-header-logo" id="org-header-logo">${renderOrgLogoMark(org)}</div>
        <div class="org-header-meta">
          <h3 class="settings-section-h" id="org-h">${escape(org.name)}</h3>
          <p class="settings-section-p" style="margin:0">
            ${escape(formatMemberCount(org.member_count))} ·
            ${escape(formatInviteCount(org.pending_invite_count))}
          </p>
        </div>
      </div>
      ${
        !isOwner
          ? `<p class="settings-org-readonly-note">Only owners can change org settings.</p>`
          : ""
      }
    </section>

    <section class="settings-section" aria-labelledby="org-name-h">
      <h3 class="settings-section-h" id="org-name-h">Organization name</h3>
      <p class="settings-section-p">Shown in the engagement deck header and the org switcher.</p>
      <form class="settings-form" id="org-name-form" novalidate>
        <label class="edit-field">
          <span class="edit-label">Name</span>
          <input id="org-name-input" class="input" type="text"
                 value="${escape(org.name)}" maxlength="200" ${isOwner ? "" : "disabled"} required />
        </label>
        ${
          isOwner
            ? `<div class="settings-form-actions">
                 <button class="btn btn-primary" type="submit">Save name</button>
                 <span class="settings-form-msg" id="org-name-msg" role="status" aria-live="polite"></span>
               </div>`
            : ""
        }
      </form>
    </section>

    <section class="settings-section" aria-labelledby="org-logo-h">
      <h3 class="settings-section-h" id="org-logo-h">Logo</h3>
      <p class="settings-section-p">PNG, JPEG, SVG, or WEBP. 500KB max. Square images work best.</p>
      <div class="logo-uploader" id="logo-uploader">
        <div class="logo-preview" id="logo-preview">${renderOrgLogoMark(org, "large")}</div>
        ${
          isOwner
            ? `<div class="logo-uploader-actions">
                 <button class="btn-secondary-sm" type="button" id="logo-pick">${org.logo_path ? "Replace logo" : "Upload logo"}</button>
                 ${org.logo_path ? `<button class="btn-ghost-sm danger" type="button" id="logo-remove">Remove</button>` : ""}
                 <input type="file" id="logo-file" hidden accept="image/png,image/jpeg,image/svg+xml,image/webp" />
                 <p class="settings-form-msg" id="logo-msg" role="status" aria-live="polite"></p>
               </div>`
            : ""
        }
      </div>
    </section>

    <section class="settings-section" aria-labelledby="org-members-h">
      <h3 class="settings-section-h" id="org-members-h">Members</h3>
      <p class="settings-section-p">${escape(formatMemberCount(args.members.length))} in this organization.</p>
      <ul class="members-list" id="members-list">${renderMembersList(args.members, isOwner, args.user.id, args.org.role)}</ul>
    </section>

    ${
      isOwner
        ? `<section class="settings-section" aria-labelledby="org-invites-h">
             <h3 class="settings-section-h" id="org-invites-h">Invite teammate</h3>
             <p class="settings-section-p">They'll get an email with a link to join. Invites expire after a week.</p>
             <form class="settings-form invite-form" id="invite-form" novalidate>
               <label class="edit-field">
                 <span class="edit-label">Email</span>
                 <input id="invite-email" class="input" type="email"
                        placeholder="teammate@example.com" autocomplete="off" required />
               </label>
               <label class="edit-field">
                 <span class="edit-label">Role</span>
                 <select id="invite-role" class="input">
                   <option value="member" selected>Member</option>
                   <option value="owner">Owner</option>
                 </select>
               </label>
               <div class="settings-form-actions">
                 <button class="btn btn-primary" type="submit">Send invite</button>
                 <span class="settings-form-msg" id="invite-msg" role="status" aria-live="polite"></span>
               </div>
             </form>

             <h3 class="settings-section-h" id="org-pending-h" style="margin-top:24px">Pending invites</h3>
             <ul class="invites-list" id="invites-list">${renderInvitesList(args.invites)}</ul>
           </section>`
        : ""
    }
  `;

  bindOrgHandlers(panel, args);
}

function bindOrgHandlers(
  panel: HTMLElement,
  args: RenderSettingsArgs,
): void {
  const { org, helpers } = args;
  const isOwner = org.role === "owner";

  if (isOwner) {
    bindOrgNameForm(panel, args);
    bindLogoUploader(panel, args);
    bindInviteForm(panel, args);
  }
  bindMembersList(panel, args);
  if (isOwner) bindPendingInvites(panel, args);

  // Quiet TS unused warnings in branches where the helpers aren't reached.
  void helpers;
}

function bindOrgNameForm(
  panel: HTMLElement,
  args: RenderSettingsArgs,
): void {
  const form = panel.querySelector<HTMLFormElement>("#org-name-form");
  const msg = panel.querySelector<HTMLElement>("#org-name-msg");
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!msg) return;
    msg.textContent = "";
    msg.classList.remove("error", "success");
    const input = panel.querySelector<HTMLInputElement>("#org-name-input");
    const next = (input?.value ?? "").trim();
    if (!next) {
      msg.textContent = "Name is required.";
      msg.classList.add("error");
      input?.focus();
      return;
    }
    try {
      const updated = await orgsApi.updateMe({ name: next });
      args.org = updated;
      msg.textContent = "Saved";
      msg.classList.add("success");
      await args.helpers.onOrgChanged();
      // Update visible labels in the org header without a full re-render.
      const h = panel.querySelector<HTMLElement>("#org-h");
      if (h) h.textContent = updated.name;
    } catch (err) {
      msg.textContent =
        err instanceof ApiError ? err.detail : "Could not save";
      msg.classList.add("error");
    }
  });
}

function bindLogoUploader(
  panel: HTMLElement,
  args: RenderSettingsArgs,
): void {
  const pickBtn = panel.querySelector<HTMLButtonElement>("#logo-pick");
  const removeBtn = panel.querySelector<HTMLButtonElement>("#logo-remove");
  const fileInput = panel.querySelector<HTMLInputElement>("#logo-file");
  const msg = panel.querySelector<HTMLElement>("#logo-msg");
  const preview = panel.querySelector<HTMLElement>("#logo-preview");
  const headerLogo = panel.querySelector<HTMLElement>("#org-header-logo");

  const setMsg = (text: string, kind: "" | "error" | "success"): void => {
    if (!msg) return;
    msg.textContent = text;
    msg.classList.remove("error", "success");
    if (kind) msg.classList.add(kind);
  };

  pickBtn?.addEventListener("click", () => fileInput?.click());

  fileInput?.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    fileInput.value = "";
    if (!file) return;

    setMsg("", "");

    // Client-side preflight: matches the backend's allow-list. The
    // server re-validates — this is purely about not pushing 25MB
    // images over the wire to get a 413 back.
    const acceptedTypes = new Set([
      "image/png",
      "image/jpeg",
      "image/svg+xml",
      "image/webp",
    ]);
    if (!acceptedTypes.has(file.type)) {
      setMsg("Use PNG, JPEG, SVG, or WEBP.", "error");
      return;
    }
    if (file.size > 500 * 1024) {
      setMsg(`That's ${(file.size / 1024).toFixed(0)}KB — keep it under 500KB.`, "error");
      return;
    }

    // Optimistic preview via a local object URL — replaced as soon as
    // the server responds with the canonical path.
    const localUrl = URL.createObjectURL(file);
    if (preview) {
      preview.innerHTML = `<img class="logo-preview-img" src="${escape(localUrl)}" alt="" />`;
    }

    if (pickBtn) {
      pickBtn.disabled = true;
      pickBtn.textContent = "Uploading...";
    }
    try {
      const { logo_path } = await orgsApi.uploadLogo(file);
      args.org = { ...args.org, logo_path };
      const url = orgLogoUrl(logo_path);
      if (preview) {
        preview.innerHTML = url
          ? `<img class="logo-preview-img" src="${escape(url)}" alt="" />`
          : renderOrgLogoMark(args.org, "large");
      }
      if (headerLogo) {
        headerLogo.innerHTML = renderOrgLogoMark(args.org);
      }
      setMsg("Logo updated", "success");
      await args.helpers.onOrgChanged();
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : "Upload failed";
      setMsg(detail, "error");
      // Revert preview.
      if (preview) preview.innerHTML = renderOrgLogoMark(args.org, "large");
    } finally {
      URL.revokeObjectURL(localUrl);
      if (pickBtn) {
        pickBtn.disabled = false;
        pickBtn.textContent = args.org.logo_path ? "Replace logo" : "Upload logo";
      }
    }
  });

  removeBtn?.addEventListener("click", () => {
    args.helpers.confirm({
      title: "Remove logo",
      body: "Clients will see the Axiolo wordmark again until you upload a new one.",
      confirmLabel: "Remove",
      danger: true,
      onConfirm: async () => {
        await orgsApi.deleteLogo();
        args.org = { ...args.org, logo_path: null };
        if (preview) preview.innerHTML = renderOrgLogoMark(args.org, "large");
        if (headerLogo) headerLogo.innerHTML = renderOrgLogoMark(args.org);
        setMsg("Logo removed", "success");
        args.helpers.toast("Logo removed");
        await args.helpers.onOrgChanged();
        // Re-render the section so the "Remove" button vanishes and
        // "Replace logo" reverts to "Upload logo".
        renderSettings(args);
      },
    });
  });
}

// ── Members ──

function renderMembersList(
  members: MemberRow[],
  isOwner: boolean,
  callerUserId: string,
  callerRole: string,
): string {
  if (members.length === 0) {
    return `<li class="members-empty">No members yet.</li>`;
  }

  // Owner safety guard: the backend rejects the last-owner demote/remove
  // (PR 3 lock_owners + count check), but we never surface an action that
  // would leave the org with zero owners. Cleanly hide the buttons —
  // don't render-then-disable — so screen readers don't announce them.
  const ownerCount = members.reduce(
    (n, m) => (m.role === "owner" ? n + 1 : n),
    0,
  );
  const isLastOwner = ownerCount === 1;

  return members
    .map((m) => {
      const isSelf = m.user_id === callerUserId;
      const displayName = m.name?.trim() || m.email;
      const isOnlyOwnerRow = m.role === "owner" && isLastOwner;
      // The "you are the only owner" helper only fires on the caller's
      // own row — other-rows are silent (their actions just hide).
      const showSelfLastOwnerHelper =
        isSelf && callerRole === "owner" && isLastOwner;
      const showDemote = m.role === "owner" && !isOnlyOwnerRow;
      const showPromote = m.role === "member";
      const showRemove = !isOnlyOwnerRow;
      const hasAnyAction = showDemote || showPromote || showRemove;

      return `
        <li class="member-row" data-user-id="${escape(m.user_id)}" data-current-role="${escape(m.role)}">
          <div class="member-row-main">
            <div class="member-row-identity">
              <span class="member-row-name">${escape(displayName)}${isSelf ? ` <span class="member-row-self">(you)</span>` : ""}</span>
              <span class="member-row-email">${escape(m.email)}</span>
            </div>
            <span class="member-row-role member-row-role--${escape(m.role)}">${escape(m.role)}</span>
          </div>
          ${
            isOwner
              ? `${
                  hasAnyAction
                    ? `<div class="member-row-actions">
                         ${showPromote ? `<button class="btn-ghost-sm" type="button" data-action="promote">Make owner</button>` : ""}
                         ${showDemote ? `<button class="btn-ghost-sm" type="button" data-action="demote">Demote to member</button>` : ""}
                         ${showRemove ? `<button class="btn-ghost-sm danger" type="button" data-action="remove">Remove</button>` : ""}
                       </div>`
                    : ""
                }
                 ${
                   showSelfLastOwnerHelper
                     ? `<p class="muted member-row-helper">Promote another member before changing your role.</p>`
                     : ""
                 }
                 <div class="member-row-confirm" role="alertdialog" aria-modal="false" hidden></div>`
              : ""
          }
        </li>
      `;
    })
    .join("");
}

function bindMembersList(
  panel: HTMLElement,
  args: RenderSettingsArgs,
): void {
  const list = panel.querySelector<HTMLElement>("#members-list");
  if (!list) return;
  list.addEventListener("click", (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn) return;
    const row = btn.closest<HTMLElement>(".member-row");
    if (!row) return;
    const userId = row.dataset.userId!;
    const currentRole = row.dataset.currentRole ?? "member";
    const action = btn.dataset.action;
    const confirmSlot = row.querySelector<HTMLElement>(".member-row-confirm");
    if (!confirmSlot) return;

    if (action === "promote" || action === "demote") {
      const next = action === "promote" ? "owner" : "member";
      const verb = action === "promote" ? "promote" : "demote";
      showInlineConfirm(confirmSlot, {
        message: `${verb === "promote" ? "Make owner" : "Demote to member"}?`,
        confirmLabel: action === "promote" ? "Make owner" : "Demote",
        onConfirm: async () => {
          await orgsApi.updateMemberRole(userId, next);
          args.helpers.toast(`Role updated`);
          await refreshOrgTab(args);
        },
      });
      void currentRole;
    } else if (action === "remove") {
      showInlineConfirm(confirmSlot, {
        message: "Remove this member from the org?",
        confirmLabel: "Remove",
        danger: true,
        onConfirm: async () => {
          await orgsApi.removeMember(userId);
          args.helpers.toast("Member removed");
          await refreshOrgTab(args);
        },
      });
    }
  });
}

interface InlineConfirmOpts {
  message: string;
  confirmLabel: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void | Promise<void>;
}

// Inline confirm row — no modal. Keeps the focus near the trigger and
// avoids the `confirm()` dialog this codebase explicitly forbids.
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
  const cancelBtn = slot.querySelector<HTMLButtonElement>("[data-inline='cancel']");
  const confirmBtn = slot.querySelector<HTMLButtonElement>("[data-inline='confirm']");

  // Esc dismisses the inline confirm — bound on document while the
  // confirm is visible, removed on cancel/confirm. Matches the modal
  // close-on-Escape pattern used elsewhere.
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
    confirmBtn.textContent = "Working...";
    try {
      await opts.onConfirm();
      // Caller refreshes the list; this slot is replaced as part of that.
      document.removeEventListener("keydown", onKey);
    } catch (err) {
      console.error("inline confirm action:", err);
      confirmBtn.disabled = false;
      confirmBtn.textContent = original;
    }
  });
  // Focus the safe action (Cancel) by default — pressing Enter on a
  // destructive confirm by accident is a worse failure mode than an
  // extra click to commit.
  cancelBtn?.focus();
}

// ── Invites ──

function renderInvitesList(invites: InviteSummary[]): string {
  if (invites.length === 0) {
    return `<li class="invites-empty">
        <span class="invites-empty-line">No pending invites.</span>
        <p class="muted invites-empty-hint">Invitations expire after 7 days.</p>
      </li>`;
  }
  return invites
    .map(
      (i) => `
      <li class="invite-row" data-invite-id="${escape(i.id)}" data-invite-email="${escape(i.email)}">
        <div class="invite-row-main">
          <div class="invite-row-identity">
            <span class="invite-row-email">${escape(i.email)}</span>
            <span class="invite-row-meta">
              ${escape(i.role)} ·
              invited ${escape(formatTimestamp(i.created_at))}${i.invited_by_email ? ` by ${escape(i.invited_by_email)}` : ""} ·
              expires ${escape(formatTimestamp(i.expires_at))}
            </span>
          </div>
          <button class="btn-ghost-sm danger" type="button" data-action="revoke-invite">Revoke</button>
        </div>
        <div class="invite-row-confirm" role="alertdialog" aria-modal="false" hidden></div>
      </li>
    `,
    )
    .join("");
}

function bindInviteForm(
  panel: HTMLElement,
  args: RenderSettingsArgs,
): void {
  const form = panel.querySelector<HTMLFormElement>("#invite-form");
  const msg = panel.querySelector<HTMLElement>("#invite-msg");
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!msg) return;
    msg.textContent = "";
    msg.classList.remove("error", "success");
    const emailInput = panel.querySelector<HTMLInputElement>("#invite-email");
    const roleSelect = panel.querySelector<HTMLSelectElement>("#invite-role");
    const email = (emailInput?.value ?? "").trim().toLowerCase();
    const role = roleSelect?.value ?? "member";

    if (!EMAIL_RE.test(email)) {
      msg.textContent = "Enter a valid email.";
      msg.classList.add("error");
      emailInput?.focus();
      return;
    }

    const submitBtn = form.querySelector<HTMLButtonElement>(
      "button[type='submit']",
    );
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Sending...";
    }

    try {
      await orgsApi.createInvite({ email, role });
      msg.textContent = `Invite sent to ${email}`;
      msg.classList.add("success");
      if (emailInput) emailInput.value = "";
      await refreshOrgTab(args);
    } catch (err) {
      msg.textContent =
        err instanceof ApiError ? err.detail : "Could not send invite";
      msg.classList.add("error");
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Send invite";
      }
    }
  });
}

function bindPendingInvites(
  panel: HTMLElement,
  args: RenderSettingsArgs,
): void {
  const list = panel.querySelector<HTMLElement>("#invites-list");
  if (!list) return;
  list.addEventListener("click", (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    const btn = target.closest<HTMLButtonElement>(
      "[data-action='revoke-invite']",
    );
    if (!btn) return;
    const row = btn.closest<HTMLElement>(".invite-row");
    if (!row) return;
    const inviteId = row.dataset.inviteId!;
    const email = row.dataset.inviteEmail ?? "this invite";
    const confirmSlot = row.querySelector<HTMLElement>(".invite-row-confirm");
    if (!confirmSlot) return;
    showInlineConfirm(confirmSlot, {
      message: `Revoke invite for ${email}? The link will stop working.`,
      confirmLabel: "Revoke",
      danger: true,
      onConfirm: async () => {
        await orgsApi.revokeInvite(inviteId);
        args.helpers.toast("Invite revoked");
        await refreshOrgTab(args);
      },
    });
  });
}

// Re-fetch the org-tab data and re-render in place. Used after any
// mutation (create/revoke invite, role change, remove member) so the
// counts and lists update without a full page reload.
async function refreshOrgTab(args: RenderSettingsArgs): Promise<void> {
  const [org, members, invites] = await Promise.all([
    orgsApi.me(),
    orgsApi.listMembers(),
    args.org.role === "owner" ? orgsApi.listInvites() : Promise.resolve([]),
  ]);
  args.org = org;
  args.members = members;
  args.invites = invites;
  renderSettings(args);
}

// ── Helpers ──

function renderOrgLogoMark(
  org: { name: string; logo_path: string | null },
  size: "default" | "large" = "default",
): string {
  const url = orgLogoUrl(org.logo_path);
  const cls = size === "large" ? "org-logo-mark org-logo-mark--lg" : "org-logo-mark";
  if (url) {
    return `<img class="${cls}" src="${escape(url)}" alt="${escape(org.name)} logo" />`;
  }
  const initial = (org.name || "?").trim().charAt(0).toUpperCase();
  return `<span class="${cls} ${cls}--fallback" aria-hidden="true">${escape(initial)}</span>`;
}

function formatMemberCount(n: number): string {
  return `${n} member${n === 1 ? "" : "s"}`;
}

function formatInviteCount(n: number): string {
  if (n === 0) return "no pending invites";
  return `${n} pending invite${n === 1 ? "" : "s"}`;
}
