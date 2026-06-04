// Invite-acceptance page (`/invite?token=...`).
//
// This page is publicly accessible — the token IS the auth. No session
// cookie is required, no admin redirect. Three terminal-status renders
// (expired / accepted / revoked / not found) and one happy path that
// branches on the operator's chosen credential method (password /
// Google / Microsoft).
import {
  ApiError,
  authApi,
  invitesApi,
  type InviteMetadata,
} from "../lib/api";

const BASE_URL = (import.meta.env.BASE_URL ?? "/") as string;

const escape = (s: string): string =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

function adminBaseHref(): string {
  return BASE_URL.endsWith("/") ? `${BASE_URL}admin/` : `${BASE_URL}/admin/`;
}

async function main(): Promise<void> {
  const mount = document.getElementById("invite-mount");
  if (!mount) return;

  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");

  if (!token) {
    renderShell(
      mount,
      `<h1 class="invite-h">No invite token</h1>
       <p class="invite-body">This link doesn't look right. Ask your teammate to resend the invite email and try again.</p>
       <a class="btn btn-secondary" href="${escape(adminBaseHref())}" style="display:block;text-align:center;margin-top:16px">Go to sign in</a>`,
    );
    return;
  }

  // Resolve the token. Any error path renders a terminal state — the
  // backend distinguishes 404 (token doesn't resolve) from a `status`
  // field on the success payload (expired/accepted/revoked).
  let meta: InviteMetadata;
  try {
    meta = await invitesApi.resolve(token);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      renderTerminal(mount, "not_found");
      return;
    }
    renderTerminal(mount, "error", err);
    return;
  }

  if (meta.status === "expired") {
    renderTerminal(mount, "expired", undefined, meta);
    return;
  }
  if (meta.status === "accepted") {
    renderTerminal(mount, "accepted", undefined, meta);
    return;
  }
  if (meta.status === "revoked") {
    renderTerminal(mount, "revoked", undefined, meta);
    return;
  }

  renderPending(mount, token, meta);
}

function renderShell(mount: HTMLElement, inner: string): void {
  // Brand chrome above the card — same markup as the signed-out admin
  // sign-in shell so the public invite-acceptance page lands inside the
  // Axiolo · Pulse identity instead of on a bare gray background.
  const baseSlash = BASE_URL.endsWith("/") ? BASE_URL : `${BASE_URL}/`;
  mount.innerHTML = `
    <div class="invite-page-shell">
      <header class="admin-header" role="banner">
        <div class="admin-header-brand">
          <span class="brand">
            <img src="${escape(baseSlash)}axiolo-logo.svg" alt="Axiolo" class="brand-logo" width="84" height="23" />
            <span class="brand-sep" aria-hidden="true">·</span>
            Pulse
          </span>
        </div>
      </header>
      <main class="invite-page">
        <div class="invite-card">${inner}</div>
      </main>
    </div>
  `;
}

// ── Pending invite (the happy path) ──

function renderPending(
  mount: HTMLElement,
  token: string,
  meta: InviteMetadata,
): void {
  const roleLabel = meta.role === "owner" ? "Owner" : "Member";

  renderShell(
    mount,
    `
    <p class="invite-eyebrow">You're invited</p>
    <h1 class="invite-h">Join ${escape(meta.org_name)}</h1>
    <p class="invite-body">
      Invitation for <strong>${escape(meta.email)}</strong> ·
      Role: <strong>${escape(roleLabel)}</strong>
    </p>

    <div class="invite-oauth-row">
      <button class="btn btn-secondary" type="button" data-provider="google">
        Continue with Google
      </button>
      <button class="btn btn-secondary" type="button" data-provider="microsoft">
        Continue with Microsoft
      </button>
    </div>

    <div class="invite-sep" role="separator" aria-label="or">— or set a password —</div>

    <form class="invite-form-block" id="invite-accept-form" novalidate>
      <label class="edit-field">
        <span class="edit-label">Your name (optional)</span>
        <input id="invite-name" class="input" type="text" autocomplete="name" />
      </label>
      <label class="edit-field">
        <span class="edit-label">Password (8+ characters)</span>
        <input id="invite-pw" class="input" type="password" autocomplete="new-password" minlength="8" required />
      </label>
      <button class="btn btn-primary" type="submit">Accept and sign in</button>
      <p class="invite-form-msg" id="invite-form-msg" role="status" aria-live="polite"></p>
    </form>

    <p class="invite-fineprint">
      Already have a Pulse account at this email? Sign in normally — the invite will be attached automatically.
    </p>
    `,
  );

  // OAuth buttons.
  for (const btn of mount.querySelectorAll<HTMLButtonElement>(
    "[data-provider]",
  )) {
    btn.addEventListener("click", async () => {
      const provider = btn.dataset.provider as "google" | "microsoft";
      btn.disabled = true;
      try {
        const { redirect_url } = await invitesApi.acceptWithOAuth(
          token,
          provider,
        );
        window.location.assign(redirect_url);
      } catch (err) {
        const detail =
          err instanceof ApiError ? err.detail : "Could not start sign-in";
        showErr(mount, detail);
        btn.disabled = false;
      }
    });
  }

  // Password form.
  const form = mount.querySelector<HTMLFormElement>("#invite-accept-form");
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const pwInput = mount.querySelector<HTMLInputElement>("#invite-pw");
    const nameInput = mount.querySelector<HTMLInputElement>("#invite-name");
    const password = pwInput?.value ?? "";
    const name = (nameInput?.value ?? "").trim();

    if (password.length < 8) {
      showErr(mount, "Password must be at least 8 characters.");
      pwInput?.focus();
      return;
    }

    const submitBtn = form.querySelector<HTMLButtonElement>(
      "button[type='submit']",
    );
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Accepting...";
    }

    try {
      await invitesApi.acceptWithPassword(token, {
        password,
        name: name || null,
      });
      // Backend sets the session cookie. Land on the admin home.
      window.location.assign(adminBaseHref());
    } catch (err) {
      // 409 = account already exists with a password. Surface that
      // distinctly so the user knows to sign in.
      if (err instanceof ApiError && err.status === 409) {
        showErr(
          mount,
          "An account already exists for this email. Sign in normally — your invite will be attached after.",
        );
      } else if (err instanceof ApiError && err.status === 410) {
        // Concurrent acceptance — re-render the terminal state.
        renderTerminal(mount, "accepted", undefined, meta);
        return;
      } else {
        const detail =
          err instanceof ApiError ? err.detail : "Could not accept invite";
        showErr(mount, detail);
      }
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Accept and sign in";
      }
    }
  });

  void authApi; // imported for future "sign in instead" wiring
}

function showErr(mount: HTMLElement, msg: string): void {
  const slot = mount.querySelector<HTMLElement>("#invite-form-msg");
  if (slot) {
    slot.textContent = msg;
    slot.classList.remove("success");
    slot.classList.add("error");
    return;
  }
  // Fallback for the OAuth path before the form is rendered: just
  // append an inline error below the OAuth buttons.
  const card = mount.querySelector<HTMLElement>(".invite-card");
  if (!card) return;
  let existing = card.querySelector<HTMLElement>(".invite-oauth-error");
  if (!existing) {
    existing = document.createElement("p");
    existing.className = "invite-oauth-error settings-form-msg error";
    card.appendChild(existing);
  }
  existing.textContent = msg;
}

// ── Terminal states ──

type TerminalKind = "expired" | "accepted" | "revoked" | "not_found" | "error";

function renderTerminal(
  mount: HTMLElement,
  kind: TerminalKind,
  errVal?: unknown,
  meta?: InviteMetadata,
): void {
  let title = "";
  let body = "";

  switch (kind) {
    case "expired":
      title = "This invite has expired";
      body = meta
        ? `Ask an owner at ${escape(meta.org_name)} to resend it.`
        : "Ask the person who invited you to resend the link.";
      break;
    case "accepted":
      title = "Invite already used";
      body = meta
        ? `This invite to ${escape(meta.org_name)} was already accepted. Sign in to continue.`
        : "This invite was already accepted. Sign in to continue.";
      break;
    case "revoked":
      title = "This invite was revoked";
      body = "Ask the person who invited you for a new link.";
      break;
    case "not_found":
      title = "Invite not found";
      body =
        "The link is invalid or has been removed. Ask your teammate to resend the invite email.";
      break;
    case "error":
    default:
      title = "Could not load invite";
      body =
        errVal instanceof ApiError
          ? errVal.detail
          : "Something went wrong on our end. Please try again.";
      break;
  }

  const showSignIn = kind === "accepted";

  renderShell(
    mount,
    `
    <h1 class="invite-h">${escape(title)}</h1>
    <p class="invite-body">${escape(body)}</p>
    <div class="invite-actions" style="margin-top:16px">
      ${
        showSignIn
          ? `<a class="btn btn-primary" href="${escape(adminBaseHref())}" style="display:block;text-align:center">Sign in</a>`
          : `<a class="btn btn-secondary" href="${escape(adminBaseHref())}" style="display:block;text-align:center">Go to sign in</a>`
      }
    </div>
    `,
  );
}

main().catch((err) => {
  console.error("invite:", err);
  const mount = document.getElementById("invite-mount");
  if (mount) renderTerminal(mount, "error", err);
});
