import {
  adminApi,
  ApiError,
  authApi,
  type AuthUser,
  type Card,
  type Client,
  type ClientResponse,
  type EngagementDetail,
  type EngagementSummary,
  type OAuthIdentitySummary,
  type ResponseType,
  type UploadRow,
} from "../lib/api";
import { formatTimestamp } from "../lib/format-time";
import {
  STATUS_VALUES,
  suggestStatus,
  type Status,
} from "../lib/status-suggest";
import {
  renderCardMarkdown,
  renderEngagementMarkdown,
  type UploadInfo,
} from "../lib/markdown-export";

const BASE_URL = (import.meta.env.BASE_URL ?? "/") as string;
const PROD_URL =
  ((import.meta.env.PUBLIC_FRONTEND_URL ?? "") as string).replace(/\/$/, "") ||
  `${window.location.origin}${BASE_URL.replace(/\/$/, "")}`;

const escape = (s: string): string =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

// ── boot ─────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  const mount = document.getElementById("admin");
  if (!mount) return;

  // Optional first-class flows triggered by query-string tokens (the
  // frontend served the verify-email / reset-password page from the same
  // route as /admin/ for simplicity).
  const params = new URLSearchParams(window.location.search);
  const verifyToken = params.get("verify-email-token");
  const resetToken = params.get("reset-password-token");

  if (verifyToken) {
    return renderVerifyEmail(mount, verifyToken);
  }
  if (resetToken) {
    return renderResetPassword(mount, resetToken);
  }

  let me: AuthUser | null = null;
  try {
    me = await authApi.me();
  } catch (err) {
    if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
      renderLogin(mount);
      return;
    }
    throw err;
  }

  if (!me.is_admin) {
    renderLogin(mount, "This account doesn't have admin access.");
    return;
  }

  void runAdmin(mount, me);
}

// ── login + signup + email flows ─────────────────────────────────────────

function renderLogin(mount: HTMLElement, errorMsg?: string): void {
  mount.innerHTML = `
    <form class="login-card" id="login-form" novalidate>
      <h1>Pulse admin</h1>
      <p>Sign in to manage engagements.</p>
      ${errorMsg ? `<div class="login-error">${escape(errorMsg)}</div>` : ""}

      <a class="btn btn-secondary" href="${escape(authApi.oauthAuthorizeUrl("google"))}" style="margin-top:8px;display:block;text-align:center">
        Continue with Google
      </a>
      <a class="btn btn-secondary" href="${escape(authApi.oauthAuthorizeUrl("microsoft"))}" style="margin-top:8px;display:block;text-align:center">
        Continue with Microsoft
      </a>

      <div style="margin:14px 0;text-align:center;color:var(--muted);font-size:12px">— or —</div>

      <label class="edit-field">
        <span class="edit-label">Email</span>
        <input id="login-email" class="input" type="email" autocomplete="email" required />
      </label>
      <label class="edit-field">
        <span class="edit-label">Password</span>
        <input id="login-pw" class="input" type="password" autocomplete="current-password" required />
      </label>

      <button class="btn btn-primary" type="submit" style="margin-top:8px">Sign in</button>

      <div style="margin-top:12px;font-size:13px;display:flex;justify-content:space-between">
        <a href="#" data-action="signup">Create account</a>
        <a href="#" data-action="forgot">Forgot password?</a>
      </div>
    </form>
  `;

  mount.querySelector<HTMLFormElement>("#login-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = (mount.querySelector<HTMLInputElement>("#login-email")?.value ?? "").trim();
    const password = mount.querySelector<HTMLInputElement>("#login-pw")?.value ?? "";
    if (!email || !password) return;

    try {
      const user = await authApi.login(email, password);
      if (!user.is_admin) {
        renderLogin(mount, "This account doesn't have admin access.");
        return;
      }
      void runAdmin(mount, user);
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : "Sign-in failed";
      renderLogin(mount, detail);
    }
  });

  mount.querySelector<HTMLAnchorElement>("[data-action='signup']")?.addEventListener("click", (e) => {
    e.preventDefault();
    renderSignup(mount);
  });
  mount.querySelector<HTMLAnchorElement>("[data-action='forgot']")?.addEventListener("click", (e) => {
    e.preventDefault();
    renderForgotPassword(mount);
  });
}

function renderSignup(mount: HTMLElement, errorMsg?: string): void {
  mount.innerHTML = `
    <form class="login-card" id="signup-form" novalidate>
      <h1>Create account</h1>
      <p>Once your email is verified, an admin must grant you admin access.</p>
      ${errorMsg ? `<div class="login-error">${escape(errorMsg)}</div>` : ""}
      <label class="edit-field">
        <span class="edit-label">Name (optional)</span>
        <input id="signup-name" class="input" type="text" autocomplete="name" />
      </label>
      <label class="edit-field">
        <span class="edit-label">Email</span>
        <input id="signup-email" class="input" type="email" autocomplete="email" required />
      </label>
      <label class="edit-field">
        <span class="edit-label">Password (8+ characters)</span>
        <input id="signup-pw" class="input" type="password" autocomplete="new-password" minlength="8" required />
      </label>
      <button class="btn btn-primary" type="submit" style="margin-top:8px">Create account</button>
      <div style="margin-top:12px;font-size:13px"><a href="#" data-action="back-to-login">← Back to sign in</a></div>
    </form>
  `;

  mount.querySelector<HTMLFormElement>("#signup-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = (mount.querySelector<HTMLInputElement>("#signup-name")?.value ?? "").trim();
    const email = (mount.querySelector<HTMLInputElement>("#signup-email")?.value ?? "").trim();
    const password = mount.querySelector<HTMLInputElement>("#signup-pw")?.value ?? "";

    try {
      await authApi.signup({ email, password, name: name || undefined });
      mount.innerHTML = `
        <div class="login-card">
          <h1>Check your email</h1>
          <p>We sent a verification link to <strong>${escape(email)}</strong>. Click it to activate the account, then sign in.</p>
          <div style="margin-top:12px;font-size:13px"><a href="#" data-action="back-to-login">← Back to sign in</a></div>
        </div>
      `;
      mount.querySelector<HTMLAnchorElement>("[data-action='back-to-login']")?.addEventListener("click", (e2) => {
        e2.preventDefault();
        renderLogin(mount);
      });
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : "Signup failed";
      renderSignup(mount, detail);
    }
  });

  mount.querySelector<HTMLAnchorElement>("[data-action='back-to-login']")?.addEventListener("click", (e) => {
    e.preventDefault();
    renderLogin(mount);
  });
}

function renderForgotPassword(mount: HTMLElement): void {
  mount.innerHTML = `
    <form class="login-card" id="forgot-form" novalidate>
      <h1>Reset password</h1>
      <p>Enter your email and we'll send a reset link.</p>
      <label class="edit-field">
        <span class="edit-label">Email</span>
        <input id="forgot-email" class="input" type="email" autocomplete="email" required />
      </label>
      <button class="btn btn-primary" type="submit" style="margin-top:8px">Send reset link</button>
      <div style="margin-top:12px;font-size:13px"><a href="#" data-action="back-to-login">← Back to sign in</a></div>
    </form>
  `;

  mount.querySelector<HTMLFormElement>("#forgot-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = (mount.querySelector<HTMLInputElement>("#forgot-email")?.value ?? "").trim();
    try {
      await authApi.forgotPassword(email);
    } catch {
      // Backend always returns 200 to avoid email enumeration. Show the
      // same confirmation regardless of error.
    }
    mount.innerHTML = `
      <div class="login-card">
        <h1>Check your email</h1>
        <p>If an account exists for <strong>${escape(email)}</strong>, you'll get a reset link shortly.</p>
        <div style="margin-top:12px;font-size:13px"><a href="#" data-action="back-to-login">← Back to sign in</a></div>
      </div>
    `;
    mount.querySelector<HTMLAnchorElement>("[data-action='back-to-login']")?.addEventListener("click", (e2) => {
      e2.preventDefault();
      renderLogin(mount);
    });
  });

  mount.querySelector<HTMLAnchorElement>("[data-action='back-to-login']")?.addEventListener("click", (e) => {
    e.preventDefault();
    renderLogin(mount);
  });
}

async function renderVerifyEmail(mount: HTMLElement, token: string): Promise<void> {
  mount.innerHTML = `<div class="login-card"><h1>Verifying email…</h1></div>`;
  try {
    await authApi.verifyEmail(token);
    mount.innerHTML = `
      <div class="login-card">
        <h1>Email verified</h1>
        <p>You can now sign in.</p>
        <a class="btn btn-primary" href="${escape(BASE_URL)}admin/" style="display:block;margin-top:12px;text-align:center">Continue</a>
      </div>
    `;
  } catch (err) {
    const detail = err instanceof ApiError ? err.detail : "Verification failed";
    mount.innerHTML = `
      <div class="login-card">
        <h1>Could not verify</h1>
        <div class="login-error">${escape(detail)}</div>
        <a class="btn btn-secondary" href="${escape(BASE_URL)}admin/" style="display:block;margin-top:12px;text-align:center">Back to sign in</a>
      </div>
    `;
  }
}

function renderResetPassword(mount: HTMLElement, token: string): void {
  mount.innerHTML = `
    <form class="login-card" id="reset-form" novalidate>
      <h1>Set new password</h1>
      <label class="edit-field">
        <span class="edit-label">New password (8+ characters)</span>
        <input id="reset-pw" class="input" type="password" autocomplete="new-password" minlength="8" required />
      </label>
      <button class="btn btn-primary" type="submit" style="margin-top:8px">Set password</button>
    </form>
  `;
  mount.querySelector<HTMLFormElement>("#reset-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const pw = mount.querySelector<HTMLInputElement>("#reset-pw")?.value ?? "";
    try {
      await authApi.resetPassword(token, pw);
      mount.innerHTML = `
        <div class="login-card">
          <h1>Password updated</h1>
          <a class="btn btn-primary" href="${escape(BASE_URL)}admin/" style="display:block;margin-top:12px;text-align:center">Sign in</a>
        </div>
      `;
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : "Could not reset password";
      mount.innerHTML = `
        <div class="login-card">
          <h1>Could not reset password</h1>
          <div class="login-error">${escape(detail)}</div>
          <a class="btn btn-secondary" href="${escape(BASE_URL)}admin/" style="display:block;margin-top:12px;text-align:center">Back to sign in</a>
        </div>
      `;
    }
  });
}

// ── after auth: routing ──────────────────────────────────────────────────

interface RouteList {
  kind: "list";
}
interface RouteDetail {
  kind: "detail";
  clientId: string;
}
interface RouteSettings {
  kind: "settings";
}
type Route = RouteList | RouteDetail | RouteSettings;

function parseRoute(): Route {
  const hash = window.location.hash.replace(/^#/, "");
  const m = hash.match(/^client\/([0-9a-f-]+)$/i);
  if (m) return { kind: "detail", clientId: m[1] };
  if (hash === "settings") return { kind: "settings" };
  return { kind: "list" };
}

async function runAdmin(mount: HTMLElement, _user: AuthUser): Promise<void> {
  mount.innerHTML = renderShell();
  attachShellHandlers(mount);

  const container = mount.querySelector<HTMLElement>(".admin-container")!;

  let route = parseRoute();
  setActiveNav(mount, route);
  await draw(container, route);

  window.addEventListener("hashchange", async () => {
    route = parseRoute();
    setActiveNav(mount, route);
    await draw(container, route);
  });
}

function renderShell(): string {
  const baseSlash = BASE_URL.endsWith("/") ? BASE_URL : `${BASE_URL}/`;
  return `
    <div class="admin-page">
      <header class="admin-header">
        <span class="brand">
          <img src="${escape(baseSlash)}axiolo-logo.svg" alt="Axiolo" class="brand-logo" width="84" height="23" />
          <span class="brand-sep" aria-hidden="true">·</span>
          Pulse
          <span class="admin-title" style="margin-left:8px">Admin</span>
        </span>
        <div class="admin-header-actions">
          <a class="admin-header-link" href="#" id="nav-engagements">Engagements</a>
          <a class="admin-header-link" href="#settings" id="nav-settings">Settings</a>
          <button class="admin-logout" type="button" id="logout">Sign out</button>
        </div>
      </header>
      <div class="admin-container">
        <div class="loading">Loading...</div>
      </div>
    </div>
  `;
}

function attachShellHandlers(mount: HTMLElement): void {
  mount.querySelector<HTMLButtonElement>("#logout")?.addEventListener("click", async () => {
    try {
      await authApi.logout();
    } catch {
      // ignore — best-effort
    }
    window.location.hash = "";
    renderLogin(mount);
  });
  mount.querySelector<HTMLAnchorElement>("#nav-engagements")?.addEventListener("click", (e) => {
    e.preventDefault();
    window.location.hash = "";
  });
}

function setActiveNav(mount: HTMLElement, route: Route): void {
  const setActive = (id: string, active: boolean) => {
    mount.querySelector<HTMLElement>(`#${id}`)?.classList.toggle("active", active);
  };
  setActive("nav-engagements", route.kind === "list" || route.kind === "detail");
  setActive("nav-settings", route.kind === "settings");
}

async function draw(container: HTMLElement, route: Route): Promise<void> {
  if (route.kind === "list") {
    container.innerHTML = `<div class="loading">Loading engagements...</div>`;
    try {
      const summaries = await adminApi.listClients();
      renderList(container, summaries);
    } catch (err) {
      console.error("load engagements:", err);
      container.innerHTML = `<div class="error"><h1 class="error-title">Could not load</h1><p class="error-body">Please refresh.</p></div>`;
    }
    return;
  }

  if (route.kind === "settings") {
    container.innerHTML = `<div class="loading">Loading settings...</div>`;
    try {
      const [me, identities] = await Promise.all([
        authApi.me(),
        authApi.listIdentities(),
      ]);
      renderSettings(container, me, identities);
    } catch (err) {
      console.error("load settings:", err);
      container.innerHTML = `<div class="error"><h1 class="error-title">Could not load</h1><p class="error-body">Please refresh.</p></div>`;
    }
    return;
  }

  container.innerHTML = `<div class="loading">Loading responses...</div>`;
  try {
    const detail = await adminApi.getClient(route.clientId);
    renderDetail(container, detail);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      container.innerHTML = `<div class="error"><h1 class="error-title">Not found</h1><p class="error-body">No client with that id.</p></div>`;
      return;
    }
    console.error("load detail:", err);
    container.innerHTML = `<div class="error"><h1 class="error-title">Could not load</h1><p class="error-body">Please refresh.</p></div>`;
  }
}

// ── settings view ───────────────────────────────────────────────────────

const PROVIDER_LABELS: Record<string, string> = {
  google: "Google",
  microsoft: "Microsoft 365",
};

function renderSettings(
  container: HTMLElement,
  user: AuthUser,
  identities: OAuthIdentitySummary[],
): void {
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

  container.innerHTML = `
    <div class="settings-page">
      <h2 class="settings-h">Settings</h2>

      <section class="settings-section">
        <h3 class="settings-section-h">Profile</h3>
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
            <span class="settings-form-msg" id="profile-msg"></span>
          </div>
        </form>
      </section>

      <section class="settings-section">
        <h3 class="settings-section-h">${escape(pwTitle)}</h3>
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
            <span class="settings-form-msg" id="password-msg"></span>
          </div>
        </form>
      </section>

      <section class="settings-section">
        <h3 class="settings-section-h">Linked accounts</h3>
        <ul class="settings-identity-list">${identityRows}</ul>
      </section>
    </div>
  `;

  // Profile form
  const profileForm = container.querySelector<HTMLFormElement>("#profile-form")!;
  const profileMsg = container.querySelector<HTMLElement>("#profile-msg")!;
  profileForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    profileMsg.textContent = "";
    profileMsg.classList.remove("error", "success");
    const nameVal = (container.querySelector<HTMLInputElement>("#profile-name")?.value ?? "").trim();
    try {
      const updated = await authApi.updateProfile({ name: nameVal || null });
      profileMsg.textContent = "Saved";
      profileMsg.classList.add("success");
      // Reflect in the input in case the server normalized it
      const input = container.querySelector<HTMLInputElement>("#profile-name");
      if (input) input.value = updated.name ?? "";
    } catch (err) {
      profileMsg.textContent =
        err instanceof ApiError ? err.detail : "Could not save";
      profileMsg.classList.add("error");
    }
  });

  // Password form
  const pwForm = container.querySelector<HTMLFormElement>("#password-form")!;
  const pwMsg = container.querySelector<HTMLElement>("#password-msg")!;
  pwForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    pwMsg.textContent = "";
    pwMsg.classList.remove("error", "success");
    const newPw = container.querySelector<HTMLInputElement>("#pw-new")?.value ?? "";
    const confirmPw = container.querySelector<HTMLInputElement>("#pw-confirm")?.value ?? "";
    const currentPw = hasPw
      ? container.querySelector<HTMLInputElement>("#pw-current")?.value ?? ""
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
      // Re-render so the form switches from "Set password" to
      // "Change password" mode and the inputs clear.
      const refreshed = await authApi.me();
      renderSettings(container, refreshed, identities);
      toast("Password updated");
    } catch (err) {
      pwMsg.textContent =
        err instanceof ApiError ? err.detail : "Could not update password";
      pwMsg.classList.add("error");
    }
  });
}

// ── list view ───────────────────────────────────────────────────────────

function renderList(container: HTMLElement, summaries: EngagementSummary[]): void {
  const header = `
    <div class="engagement-list-header">
      <h2 class="engagement-list-h">Engagements</h2>
      <button class="btn-primary-sm" type="button" data-action="new-engagement">+ New engagement</button>
    </div>
  `;

  if (summaries.length === 0) {
    container.innerHTML = `
      ${header}
      <div class="empty-card">
        <p>No engagements yet. Click + New engagement to create your first one.</p>
      </div>
    `;
    container
      .querySelector<HTMLButtonElement>("[data-action='new-engagement']")
      ?.addEventListener("click", () => openNewEngagementModal(container));
    return;
  }

  const rows = summaries
    .map((s) => {
      const completed = s.answered_count + s.skipped_count;
      return `
      <tr data-client-id="${escape(s.id)}">
        <td>
          <div class="client-name">${escape(s.name)}</div>
          <div class="org-name">${escape(s.org_name ?? "")}</div>
        </td>
        <td>${escape(s.engagement_name ?? "")}</td>
        <td>
          <span class="progress-pill">${completed} / ${s.total_cards}</span>
        </td>
        <td class="last-active">${escape(formatTimestamp(s.last_active_at))}</td>
        <td class="actions">
          <button class="action-link" type="button" data-action="view">View responses</button>
          <button class="action-link" type="button" data-action="copy-link">Copy link</button>
          <button class="action-link danger" type="button" data-action="rotate">Rotate token</button>
        </td>
      </tr>`;
    })
    .join("");

  container.innerHTML = `
    ${header}
    <div class="engagement-table-wrap">
      <table class="engagement-table">
        <thead>
          <tr>
            <th>Client</th>
            <th>Engagement</th>
            <th>Progress</th>
            <th>Last active</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;

  container
    .querySelector<HTMLButtonElement>("[data-action='new-engagement']")
    ?.addEventListener("click", () => openNewEngagementModal(container));

  container.addEventListener("click", async (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "new-engagement") return; // handled above

    const row = btn.closest<HTMLElement>("tr[data-client-id]");
    if (!row) return;
    const clientId = row.dataset.clientId!;

    const summary = summaries.find((s) => s.id === clientId);
    if (!summary) return;

    switch (action) {
      case "view":
        window.location.hash = `client/${clientId}`;
        return;
      case "copy-link":
        await navigator.clipboard.writeText(`${PROD_URL}?t=${summary.token}`);
        toast("Link copied to clipboard");
        return;
      case "rotate": {
        const ok = window.confirm(
          `Rotate ${summary.name}'s token? The current link will stop working immediately.`
        );
        if (!ok) return;
        await rotateToken(container, summary.id);
        return;
      }
    }
  });
}

// ── new engagement modal ────────────────────────────────────────────────

function openNewEngagementModal(container: HTMLElement): void {
  const modalEl = document.createElement("div");
  modalEl.className = "modal";
  modalEl.innerHTML = `
    <div class="modal-backdrop" data-close></div>
    <div class="modal-panel new-eng-panel">
      <header class="modal-header">
        <span class="modal-title">New engagement</span>
        <button class="modal-close" type="button" data-close aria-label="Close">×</button>
      </header>
      <form class="new-eng-form" id="new-eng-form">
        <label class="edit-field">
          <span class="edit-label">Client name (required)</span>
          <input class="input" id="ne-name" type="text" autofocus required />
        </label>
        <label class="edit-field">
          <span class="edit-label">Organization (optional)</span>
          <input class="input" id="ne-org" type="text" />
        </label>
        <label class="edit-field">
          <span class="edit-label">Engagement name (optional)</span>
          <input class="input" id="ne-eng" type="text" />
        </label>
        <div class="edit-actions">
          <button class="btn-primary-sm" type="submit">Create engagement</button>
          <button class="btn-ghost-sm" type="button" data-close>Cancel</button>
        </div>
      </form>
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

  modalEl.querySelector<HTMLFormElement>("#new-eng-form")!.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = (modalEl.querySelector<HTMLInputElement>("#ne-name")?.value ?? "").trim();
    const org = (modalEl.querySelector<HTMLInputElement>("#ne-org")?.value ?? "").trim();
    const eng = (modalEl.querySelector<HTMLInputElement>("#ne-eng")?.value ?? "").trim();
    if (!name) {
      modalEl.querySelector<HTMLInputElement>("#ne-name")?.focus();
      return;
    }

    const submitBtn = modalEl.querySelector<HTMLButtonElement>("button[type='submit']");
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Creating...";
    }

    try {
      const created = await adminApi.createClient({
        name,
        org_name: org || null,
        engagement_name: eng || null,
      });
      close();
      toast(`Engagement created for ${created.name}`);
      window.location.hash = `client/${created.id}`;
      void container;
    } catch (err) {
      console.error("create engagement:", err);
      toast("Could not create engagement");
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Create engagement";
      }
    }
  });
}

// ── edit engagement modal ───────────────────────────────────────────────

function renderDetailHeader(client: Client): string {
  const subtitle = [client.org_name, client.engagement_name]
    .filter((s) => s && s.trim().length > 0)
    .map((s) => escape(s as string))
    .join(" · ");
  return `
    <div>
      <h2>${escape(client.name)}</h2>
      ${subtitle ? `<div class="org">${subtitle}</div>` : ""}
    </div>
    <div class="detail-actions">
      <button class="btn-secondary-sm" type="button" id="edit-engagement">Edit details</button>
      <button class="btn-secondary-sm" type="button" id="download-md">Download as Markdown</button>
      <button class="btn-secondary-sm" type="button" id="copy-all">Copy all as Markdown</button>
      <button class="btn-secondary-sm" type="button" id="copy-link">Copy link</button>
    </div>
  `;
}

function openEditEngagementModal(
  client: Client & { token: string },
  onSaved: (updated: { name: string; org_name: string | null; engagement_name: string | null }) => void,
): void {
  const modalEl = document.createElement("div");
  modalEl.className = "modal";
  modalEl.innerHTML = `
    <div class="modal-backdrop" data-close></div>
    <div class="modal-panel new-eng-panel">
      <header class="modal-header">
        <span class="modal-title">Edit engagement</span>
        <button class="modal-close" type="button" data-close aria-label="Close">×</button>
      </header>
      <form class="new-eng-form" id="edit-eng-form">
        <label class="edit-field">
          <span class="edit-label">Client name (required)</span>
          <input class="input" id="ee-name" type="text" autofocus required value="${escape(client.name)}" />
        </label>
        <label class="edit-field">
          <span class="edit-label">Organization (optional)</span>
          <input class="input" id="ee-org" type="text" value="${escape(client.org_name ?? "")}" />
        </label>
        <label class="edit-field">
          <span class="edit-label">Engagement name (optional)</span>
          <input class="input" id="ee-eng" type="text" value="${escape(client.engagement_name ?? "")}" />
        </label>
        <div class="edit-actions">
          <button class="btn-primary-sm" type="submit">Save changes</button>
          <button class="btn-ghost-sm" type="button" data-close>Cancel</button>
        </div>
      </form>
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

  modalEl.querySelector<HTMLFormElement>("#edit-eng-form")!.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = (modalEl.querySelector<HTMLInputElement>("#ee-name")?.value ?? "").trim();
    const org = (modalEl.querySelector<HTMLInputElement>("#ee-org")?.value ?? "").trim();
    const eng = (modalEl.querySelector<HTMLInputElement>("#ee-eng")?.value ?? "").trim();
    if (!name) {
      modalEl.querySelector<HTMLInputElement>("#ee-name")?.focus();
      return;
    }

    const submitBtn = modalEl.querySelector<HTMLButtonElement>("button[type='submit']");
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Saving...";
    }

    try {
      const updated = await adminApi.updateClient(client.id, {
        name,
        org_name: org || null,
        engagement_name: eng || null,
      });
      onSaved({
        name: updated.name,
        org_name: updated.org_name,
        engagement_name: updated.engagement_name,
      });
      close();
      toast("Engagement updated");
    } catch (err) {
      console.error("update engagement:", err);
      toast("Could not save changes");
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Save changes";
      }
    }
  });
}

async function rotateToken(container: HTMLElement, clientId: string): Promise<void> {
  try {
    const updated = await adminApi.rotateToken(clientId);
    await navigator.clipboard.writeText(`${PROD_URL}?t=${updated.token}`);
    toast("New token copied to clipboard");
  } catch (err) {
    console.error("rotate token:", err);
    toast("Could not rotate token");
    return;
  }
  await draw(container, { kind: "list" });
}

// ── detail view ──────────────────────────────────────────────────────────

interface DetailViewData {
  client: Client & { token: string };
  cards: Card[];
  responses: Map<string, ClientResponse>;
  uploads: Map<string, UploadRow[]>;
}

function bucketDetail(payload: EngagementDetail): DetailViewData {
  const responses = new Map<string, ClientResponse>();
  for (const r of payload.responses) responses.set(r.card_id, r);
  const uploads = new Map<string, UploadRow[]>();
  for (const u of payload.uploads) {
    const list = uploads.get(u.card_id) ?? [];
    list.push(u);
    uploads.set(u.card_id, list);
  }
  return { client: payload.client, cards: payload.cards, responses, uploads };
}

function renderDetail(container: HTMLElement, payload: EngagementDetail): void {
  const data = bucketDetail(payload);
  const { client, cards, responses, uploads } = data;

  const statusOverrides = new Map<string, Status>();

  const cardsHtml = cards
    .map((card) => renderResponseCard(card, responses.get(card.id), uploads.get(card.id) ?? [], statusOverrides))
    .join("");

  container.innerHTML = `
    <button class="back-link" type="button" id="back">← All engagements</button>
    <section class="detail-header" id="detail-header">${renderDetailHeader(client)}</section>
    <section id="brief-slot">${renderBriefView(client)}</section>
    <div id="cards-list">${cardsHtml}</div>
    <div id="add-card-slot">
      <div class="add-card-bar">
        <button class="btn-primary-sm add-card-btn" type="button" id="add-card-trigger">+ Add card</button>
        <button class="btn-secondary-sm" type="button" id="import-markdown-trigger">Upload as Markdown</button>
        <input type="file" id="import-markdown-file" accept=".md,text/markdown,text/plain" hidden />
      </div>
      <div class="import-error" id="import-error" hidden></div>
    </div>
  `;

  container.querySelector<HTMLButtonElement>("#back")?.addEventListener("click", () => {
    window.location.hash = "";
  });

  const headerEl = container.querySelector<HTMLElement>("#detail-header")!;

  const bindHeaderActions = (): void => {
    headerEl.querySelector<HTMLButtonElement>("#copy-link")?.addEventListener("click", async () => {
      await navigator.clipboard.writeText(`${PROD_URL}?t=${client.token}`);
      toast("Link copied to clipboard");
    });

    headerEl.querySelector<HTMLButtonElement>("#copy-all")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      btn.disabled = true;
      try {
        const md = buildEngagementMarkdown(data, statusOverrides);
        await navigator.clipboard.writeText(md);
        flashCopied(btn, "Copied!");
        toast("All cards copied as Markdown");
      } catch (err) {
        console.error("copy all:", err);
        toast("Could not copy");
      } finally {
        btn.disabled = false;
      }
    });

    headerEl.querySelector<HTMLButtonElement>("#download-md")?.addEventListener("click", (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      try {
        const md = buildEngagementMarkdown(data, statusOverrides);
        triggerDownload(md, downloadFilename(client));
        flashCopied(btn, "Downloaded");
        toast(`Saved ${downloadFilename(client)}`);
      } catch (err) {
        console.error("download:", err);
        toast("Could not download");
      }
    });

    headerEl.querySelector<HTMLButtonElement>("#edit-engagement")?.addEventListener("click", () => {
      openEditEngagementModal(client, (updated) => {
        client.name = updated.name;
        client.org_name = updated.org_name;
        client.engagement_name = updated.engagement_name;
        headerEl.innerHTML = renderDetailHeader(client);
        bindHeaderActions();
      });
    });
  };

  bindHeaderActions();

  // ── Brief ────────────────────────────────────────────────────────────
  const briefSlot = container.querySelector<HTMLElement>("#brief-slot")!;

  const showBriefView = (): void => {
    briefSlot.innerHTML = renderBriefView(client);
    briefSlot.querySelector<HTMLButtonElement>("[data-action='brief-edit']")?.addEventListener("click", showBriefEdit);
    briefSlot.querySelector<HTMLButtonElement>("[data-action='brief-add']")?.addEventListener("click", showBriefEdit);
    briefSlot.querySelector<HTMLButtonElement>("[data-action='brief-copy']")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      await navigator.clipboard.writeText(client.brief ?? "");
      flashCopied(btn, "Copied!");
      toast("Brief copied as Markdown");
    });
  };

  const showBriefEdit = (): void => {
    briefSlot.innerHTML = renderBriefEdit(client);
    const ta = briefSlot.querySelector<HTMLTextAreaElement>("#brief-textarea")!;
    ta.focus();
    ta.setSelectionRange(0, 0);

    briefSlot.querySelector<HTMLButtonElement>("[data-action='brief-cancel']")?.addEventListener("click", showBriefView);
    briefSlot.querySelector<HTMLButtonElement>("[data-action='brief-save']")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      const next = ta.value;
      btn.disabled = true;
      const orig = btn.textContent;
      btn.textContent = "Saving...";
      try {
        await adminApi.updateClient(client.id, { brief: next || null });
        client.brief = next || null;
        toast("Brief saved");
        showBriefView();
      } catch (err) {
        console.error("brief save:", err);
        toast("Could not save brief");
      } finally {
        btn.disabled = false;
        btn.textContent = orig;
      }
    });
  };

  showBriefView();

  // ── Per-card handlers ────────────────────────────────────────────────
  const attachCardHandlers = (articleEl: HTMLElement): void => {
    const cardId = articleEl.dataset.cardId!;
    const card = cards.find((c) => c.id === cardId);
    if (!card) return;

    const select = articleEl.querySelector<HTMLSelectElement>(".status-select");
    select?.addEventListener("change", () => {
      statusOverrides.set(cardId, select.value as Status);
    });

    articleEl.querySelector<HTMLButtonElement>("[data-action='copy-card']")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      btn.disabled = true;
      try {
        const md = buildSingleCardMarkdown(
          client,
          card,
          responses.get(card.id),
          uploads.get(card.id) ?? [],
          statusOverrides.get(card.id)
        );
        await navigator.clipboard.writeText(md);
        flashCopied(btn, "Copied!");
      } catch (err) {
        console.error("copy card:", err);
        toast("Could not copy");
      } finally {
        btn.disabled = false;
      }
    });

    articleEl.querySelector<HTMLButtonElement>("[data-action='edit-card-start']")?.addEventListener("click", () => {
      swapCardHtml(articleEl, renderEditCardForm(card));
    });

    articleEl.querySelector<HTMLButtonElement>("[data-action='edit-card-cancel']")?.addEventListener("click", () => {
      swapCardHtml(
        articleEl,
        renderResponseCard(card, responses.get(card.id), uploads.get(card.id) ?? [], statusOverrides)
      );
    });

    articleEl.querySelector<HTMLButtonElement>("[data-action='edit-card-save']")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      const patch = readEditForm(articleEl, card);
      if (!patch) return;
      btn.disabled = true;
      const original = btn.textContent;
      btn.textContent = "Saving...";
      try {
        const updated = await adminApi.updateCard(card.id, patch);
        const idx = cards.findIndex((c) => c.id === card.id);
        if (idx >= 0) cards[idx] = updated;
        swapCardHtml(
          articleEl,
          renderResponseCard(updated, responses.get(card.id), uploads.get(card.id) ?? [], statusOverrides)
        );
        toast("Card saved");
      } catch (err) {
        console.error("card update:", err);
        toast("Could not save");
      } finally {
        btn.disabled = false;
        btn.textContent = original;
      }
    });

    articleEl.querySelector<HTMLButtonElement>("[data-action='delete-card']")?.addEventListener("click", async () => {
      const responseCount =
        responses.get(card.id) && responses.get(card.id)!.state !== "viewed" ? "an existing response" : null;
      const uploadList = uploads.get(card.id) ?? [];
      const warningParts: string[] = [];
      if (responseCount) warningParts.push("the response on file");
      if (uploadList.length) warningParts.push(`${uploadList.length} uploaded file${uploadList.length === 1 ? "" : "s"}`);
      const warning = warningParts.length
        ? `\n\nThis will also remove ${warningParts.join(" and ")}. This cannot be undone.`
        : "\n\nThis cannot be undone.";
      const ok = window.confirm(`Delete card ${card.order_index}: "${card.title}"?${warning}`);
      if (!ok) return;

      try {
        await adminApi.deleteCard(card.id);
      } catch (err) {
        console.error("delete card:", err);
        toast("Could not delete");
        return;
      }

      const idx = cards.findIndex((c) => c.id === card.id);
      if (idx >= 0) cards.splice(idx, 1);
      responses.delete(card.id);
      uploads.delete(card.id);
      articleEl.remove();
      toast("Card deleted");
    });
  };

  const swapCardHtml = (articleEl: HTMLElement, newHtml: string): void => {
    const tmp = document.createElement("div");
    tmp.innerHTML = newHtml.trim();
    const next = tmp.firstElementChild as HTMLElement | null;
    if (!next) return;
    articleEl.replaceWith(next);
    attachCardHandlers(next);
  };

  for (const articleEl of container.querySelectorAll<HTMLElement>(".response-card[data-card-id]")) {
    attachCardHandlers(articleEl);
  }

  // ── Add card flow ────────────────────────────────────────────────────
  const addCardSlot = container.querySelector<HTMLElement>("#add-card-slot")!;
  const cardsList = container.querySelector<HTMLElement>("#cards-list")!;

  const showAddCardTrigger = (): void => {
    addCardSlot.innerHTML = `
      <div class="add-card-bar">
        <button class="btn-primary-sm add-card-btn" type="button" id="add-card-trigger">+ Add card</button>
        <button class="btn-secondary-sm" type="button" id="import-markdown-trigger">Upload as Markdown</button>
        <input type="file" id="import-markdown-file" accept=".md,text/markdown,text/plain" hidden />
      </div>
      <div class="import-error" id="import-error" hidden></div>
    `;
    addCardSlot.querySelector<HTMLButtonElement>("#add-card-trigger")?.addEventListener("click", () => {
      clearImportError();
      showAddCardForm();
    });
    wireImportMarkdownButton();
  };

  const renderImportError = (detail: string): void => {
    const slot = addCardSlot.querySelector<HTMLElement>("#import-error");
    if (!slot) return;
    const lines = detail.split("\n").map((s) => s.trim()).filter(Boolean);
    const body =
      lines.length > 1
        ? `<ul class="import-error-list">${lines
            .map((l) => `<li>${escape(l)}</li>`)
            .join("")}</ul>`
        : `<p class="import-error-body">${escape(lines[0] ?? detail)}</p>`;
    slot.innerHTML = `
      <div class="import-error-head">
        <strong>Import failed</strong>
        <button type="button" class="import-error-dismiss" aria-label="Dismiss">×</button>
      </div>
      ${body}
    `;
    slot.hidden = false;
    slot.querySelector<HTMLButtonElement>(".import-error-dismiss")?.addEventListener("click", clearImportError);
  };

  const clearImportError = (): void => {
    const slot = addCardSlot.querySelector<HTMLElement>("#import-error");
    if (!slot) return;
    slot.innerHTML = "";
    slot.hidden = true;
  };

  const wireImportMarkdownButton = (): void => {
    const trigger = addCardSlot.querySelector<HTMLButtonElement>("#import-markdown-trigger");
    const fileInput = addCardSlot.querySelector<HTMLInputElement>("#import-markdown-file");
    if (!trigger || !fileInput) return;

    trigger.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", async () => {
      const file = fileInput.files?.[0];
      // Reset so picking the same file twice still fires `change`.
      fileInput.value = "";
      if (!file) return;

      clearImportError();
      const originalLabel = trigger.textContent ?? "Upload as Markdown";
      trigger.disabled = true;
      trigger.textContent = "Importing...";
      try {
        const markdown = await file.text();
        const { created } = await adminApi.importMarkdownCards(client.id, markdown);
        for (const card of created) {
          cards.push(card);
          const tmp = document.createElement("div");
          tmp.innerHTML = renderResponseCard(card, undefined, [], statusOverrides).trim();
          const next = tmp.firstElementChild as HTMLElement | null;
          if (next) {
            cardsList.appendChild(next);
            attachCardHandlers(next);
          }
        }
        toast(`${created.length} card${created.length === 1 ? "" : "s"} imported`);
      } catch (err) {
        const detail = err instanceof ApiError ? err.detail : "Could not import markdown";
        console.error("import markdown:", err);
        renderImportError(detail);
      } finally {
        trigger.disabled = false;
        trigger.textContent = originalLabel;
      }
    });
  };

  const showAddCardForm = (): void => {
    addCardSlot.innerHTML = renderAddCardForm();
    const formEl = addCardSlot.querySelector<HTMLElement>(".response-card.is-editing")!;
    const typeSelect = formEl.querySelector<HTMLSelectElement>(".add-type")!;
    const optionsField = formEl.querySelector<HTMLElement>(".add-options-field")!;
    const showOrHideOptions = (): void => {
      const t = typeSelect.value;
      optionsField.style.display = t === "single-select" || t === "multi-select" ? "" : "none";
    };
    typeSelect.addEventListener("change", showOrHideOptions);
    showOrHideOptions();

    formEl.querySelector<HTMLButtonElement>("[data-action='add-card-cancel']")?.addEventListener("click", showAddCardTrigger);
    formEl.querySelector<HTMLButtonElement>("[data-action='add-card-save']")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      const newCard = readAddForm(formEl);
      if (!newCard) return;
      btn.disabled = true;
      const original = btn.textContent;
      btn.textContent = "Saving...";
      try {
        const created = await adminApi.createCard(client.id, newCard);
        cards.push(created);
        const tmp = document.createElement("div");
        tmp.innerHTML = renderResponseCard(created, undefined, [], statusOverrides).trim();
        const next = tmp.firstElementChild as HTMLElement | null;
        if (next) {
          cardsList.appendChild(next);
          attachCardHandlers(next);
        }
        showAddCardTrigger();
        toast("Card added");
      } catch (err) {
        console.error("create card:", err);
        toast("Could not create card");
      } finally {
        btn.disabled = false;
        btn.textContent = original;
      }
    });
  };

  addCardSlot.querySelector<HTMLButtonElement>("#add-card-trigger")?.addEventListener("click", () => {
    clearImportError();
    showAddCardForm();
  });
  wireImportMarkdownButton();
}

interface AddCardPayload {
  category: string;
  title: string;
  context: string;
  question: string;
  response_type: ResponseType;
  options: string[] | null;
  default_value: string | null;
  skip_allowed: boolean;
  attachment_path: string | null;
}

function readAddForm(formEl: HTMLElement): AddCardPayload | null {
  const title = (formEl.querySelector<HTMLInputElement>(".add-title")?.value ?? "").trim();
  const category = (formEl.querySelector<HTMLInputElement>(".add-category")?.value ?? "").trim();
  const context = (formEl.querySelector<HTMLTextAreaElement>(".add-context")?.value ?? "").trim();
  const question = (formEl.querySelector<HTMLTextAreaElement>(".add-question")?.value ?? "").trim();
  const responseType = (formEl.querySelector<HTMLSelectElement>(".add-type")?.value ?? "") as ResponseType;
  const optionsRaw = formEl.querySelector<HTMLTextAreaElement>(".add-options")?.value ?? "";
  const defaultValue = (formEl.querySelector<HTMLTextAreaElement>(".add-default")?.value ?? "").trim();
  const attachment = (formEl.querySelector<HTMLInputElement>(".add-attachment")?.value ?? "").trim();
  const skipAllowed = formEl.querySelector<HTMLInputElement>(".add-skip")?.checked ?? true;

  for (const [sel, val] of [
    [".add-title", title],
    [".add-category", category],
    [".add-context", context],
    [".add-question", question],
  ] as const) {
    if (!val) {
      formEl.querySelector<HTMLElement>(sel)?.focus();
      toast("All four required fields must be filled");
      return null;
    }
  }

  const isSelect = responseType === "single-select" || responseType === "multi-select";
  let options: string[] | null = null;
  if (isSelect) {
    options = optionsRaw.split("\n").map((s) => s.trim()).filter(Boolean);
    if (options.length === 0) {
      formEl.querySelector<HTMLElement>(".add-options")?.focus();
      toast("At least one option is required");
      return null;
    }
  }

  return {
    title,
    category,
    context,
    question,
    response_type: responseType,
    options,
    default_value: defaultValue || null,
    attachment_path: attachment || null,
    skip_allowed: skipAllowed,
  };
}

function renderAddCardForm(): string {
  return `
    <article class="response-card is-editing add-card-form">
      <div class="response-card-head">
        <div>
          <div class="card-num">New card</div>
          <h3 class="card-h">Add a card</h3>
        </div>
      </div>

      <div class="edit-grid">
        <label class="edit-field">
          <span class="edit-label">Title</span>
          <input class="input add-title" type="text" />
        </label>

        <label class="edit-field">
          <span class="edit-label">Category</span>
          <input class="input add-category" type="text" placeholder="e.g. Decisions" />
        </label>

        <label class="edit-field">
          <span class="edit-label">Response type</span>
          <select class="input add-type">
            <option value="confirm-edit">confirm-edit</option>
            <option value="single-select">single-select</option>
            <option value="multi-select">multi-select</option>
            <option value="short-text">short-text</option>
            <option value="long-text">long-text</option>
            <option value="document-link">document-link</option>
            <option value="contact-share">contact-share</option>
            <option value="file-upload">file-upload</option>
          </select>
        </label>

        <label class="edit-field">
          <span class="edit-label">Context</span>
          <textarea class="textarea add-context" rows="6"></textarea>
        </label>

        <label class="edit-field">
          <span class="edit-label">Question</span>
          <textarea class="textarea add-question" rows="3"></textarea>
        </label>

        <label class="edit-field add-options-field">
          <span class="edit-label">Options (one per line, only for select types)</span>
          <textarea class="textarea add-options" rows="4"></textarea>
        </label>

        <label class="edit-field">
          <span class="edit-label">Default value (optional, used by confirm-edit)</span>
          <textarea class="textarea add-default" rows="2"></textarea>
        </label>

        <label class="edit-field">
          <span class="edit-label">Active reference path (optional)</span>
          <input class="input add-attachment" type="text" placeholder="deliverables/example.html" />
        </label>

        <label class="edit-toggle">
          <input class="add-skip" type="checkbox" checked />
          <span>Skip allowed</span>
        </label>
      </div>

      <div class="edit-actions">
        <button class="btn-primary-sm" type="button" data-action="add-card-save">Save card</button>
        <button class="btn-ghost-sm" type="button" data-action="add-card-cancel">Cancel</button>
      </div>
    </article>
  `;
}

// ── Brief templates ─────────────────────────────────────────────────────

const BRIEF_TEMPLATE = `# <Client name> · <Engagement name>

**Status:** Drafting / Active / Complete / Paused
**URL:** *(pulled from Copy link above)*
**Sent:** *(date)*
**Cards:** *(N)*

---

## 1. Client profile

**Name:** <full name>
**Role and org:**
**How we met:**

### Behavioral profile
- Mobile-first or desktop-first?
- Tappable, willing to type, voice-friendly?
- Time-starved? Specific time windows when they're reachable?
- Numbers-comfortable, or does dyscalculia / number anxiety apply?
- Communication style: direct? layered?
- Any other quirks: language, time zone, vision, attention rhythms.

### Representative quote
> *(a real message or transcript snippet so anyone reading can hear them)*

### What this means for the deck
- Card order
- Tone (which words to use, which to avoid)
- Response types to favor
- Skip policy

---

## 2. Engagement context

What this engagement is trying to validate, unblock, or align.

- Source material (transcripts, business plans, prior work)
- Open items
- Decisions we're trying to surface

---

## 3. The card deck

| # | Title | Type | Skip |
|---|---|---|---|
| 1 | … | confirm-edit | required |

---

## 4. Active References

Any HTML deliverables for this engagement. Drop files in \`public/deliverables/\` and wire them via the Edit form on each card.

---

## 5. Operations log

- **YYYY-MM-DD** — sent the link, …

---

## 6. Handoff

- [ ] All required cards answered
- [ ] Responses exported to ClickUp
- [ ] Token rotated or revoked if access should end
`;

function renderBriefView(client: Client): string {
  const hasBrief = !!(client.brief && client.brief.trim().length > 0);
  if (!hasBrief) {
    return `
      <div class="brief-card brief-empty">
        <div class="brief-head">
          <span class="brief-label">Engagement brief</span>
          <button class="btn-primary-sm" type="button" data-action="brief-add">+ Write brief</button>
        </div>
        <p class="brief-empty-body">
          A one-page narrative for this engagement: who the client is, how they move,
          what we're validating. Editable here, copyable as Markdown to share.
        </p>
      </div>
    `;
  }
  return `
    <div class="brief-card">
      <div class="brief-head">
        <span class="brief-label">Engagement brief</span>
        <div class="brief-actions">
          <button class="btn-ghost-sm" type="button" data-action="brief-copy">Copy as Markdown</button>
          <button class="btn-ghost-sm" type="button" data-action="brief-edit">Edit</button>
        </div>
      </div>
      <pre class="brief-body">${escape(client.brief ?? "")}</pre>
    </div>
  `;
}

function renderBriefEdit(client: Client): string {
  const value = client.brief && client.brief.trim().length > 0 ? client.brief : BRIEF_TEMPLATE;
  return `
    <div class="brief-card brief-editing">
      <div class="brief-head">
        <span class="brief-label">Editing engagement brief</span>
        <div class="brief-actions">
          <button class="btn-primary-sm" type="button" data-action="brief-save">Save brief</button>
          <button class="btn-ghost-sm" type="button" data-action="brief-cancel">Cancel</button>
        </div>
      </div>
      <textarea id="brief-textarea" class="brief-textarea" rows="22">${escape(value)}</textarea>
    </div>
  `;
}

// ── Card rendering helpers ──────────────────────────────────────────────

function renderResponseCard(
  card: Card,
  response: ClientResponse | undefined,
  uploads: UploadRow[],
  statusOverrides: Map<string, Status>
): string {
  const suggested = suggestStatus(card, response);
  statusOverrides.set(card.id, suggested);

  const stateLabel = labelFor(response);
  const stateClass = stateClassFor(response);

  const optionsHtml = STATUS_VALUES.map(
    (s) => `<option value="${escape(s)}"${s === suggested ? " selected" : ""}>${escape(s)}</option>`
  ).join("");

  return `
    <article class="response-card" data-card-id="${escape(card.id)}">
      <div class="response-card-head">
        <div>
          <div class="card-num">Card ${card.order_index} · ${escape(card.category)}</div>
          <h3 class="card-h">${escape(card.title)}</h3>
        </div>
        <div class="response-card-head-right">
          <span class="response-state ${stateClass}">${escape(stateLabel)}</span>
          <button class="btn-ghost-sm" type="button" data-action="edit-card-start" title="Edit card text">Edit</button>
          <button class="btn-ghost-sm danger" type="button" data-action="delete-card" title="Delete this card">Delete</button>
        </div>
      </div>
      <div class="response-body${responseBodyMutedClass(response)}">${renderResponseBodyHtml(card, response, uploads)}</div>
      <div class="response-meta">
        <div class="response-meta-left">
          <span>Suggested status:</span>
          <select class="status-select">${optionsHtml}</select>
        </div>
        <div class="response-meta-right">
          ${response?.answered_at ? `<span>Answered ${escape(formatTimestamp(response.answered_at))}</span>` : response?.viewed_at ? `<span>Viewed ${escape(formatTimestamp(response.viewed_at))}</span>` : ""}
          <button class="btn-primary-sm" type="button" data-action="copy-card" style="margin-left:12px">Copy</button>
        </div>
      </div>
    </article>
  `;
}

function readEditForm(articleEl: HTMLElement, card: Card): Partial<Card> | null {
  const titleEl = articleEl.querySelector<HTMLInputElement>(".edit-title");
  const categoryEl = articleEl.querySelector<HTMLInputElement>(".edit-category");
  const contextEl = articleEl.querySelector<HTMLTextAreaElement>(".edit-context");
  const questionEl = articleEl.querySelector<HTMLTextAreaElement>(".edit-question");
  const optionsEl = articleEl.querySelector<HTMLTextAreaElement>(".edit-options");
  const attachmentEl = articleEl.querySelector<HTMLInputElement>(".edit-attachment");
  const skipEl = articleEl.querySelector<HTMLInputElement>(".edit-skip");

  const title = (titleEl?.value ?? "").trim();
  const category = (categoryEl?.value ?? "").trim();
  const context = (contextEl?.value ?? "").trim();
  const question = (questionEl?.value ?? "").trim();
  const attachment = (attachmentEl?.value ?? "").trim();

  for (const [el, val] of [
    [titleEl, title],
    [categoryEl, category],
    [contextEl, context],
    [questionEl, question],
  ] as const) {
    if (!val) {
      el?.focus();
      toast("All four required fields must be filled");
      return null;
    }
  }

  const isSelect = card.response_type === "single-select" || card.response_type === "multi-select";
  let options: string[] | null | undefined;
  if (isSelect && optionsEl) {
    options = optionsEl.value.split("\n").map((s) => s.trim()).filter(Boolean);
    if (options.length === 0) {
      optionsEl.focus();
      toast("At least one option is required");
      return null;
    }
  }

  const patch: Partial<Card> = {
    title,
    category,
    context,
    question,
    skip_allowed: skipEl?.checked ?? card.skip_allowed,
    attachment_path: attachment || null,
  };
  if (isSelect) patch.options = options ?? null;
  return patch;
}

function renderEditCardForm(card: Card): string {
  const isSelect = card.response_type === "single-select" || card.response_type === "multi-select";
  const optionsText = isSelect && card.options ? card.options.join("\n") : "";
  return `
    <article class="response-card is-editing" data-card-id="${escape(card.id)}">
      <div class="response-card-head">
        <div>
          <div class="card-num">Card ${card.order_index} · editing</div>
          <h3 class="card-h">${escape(card.title)}</h3>
        </div>
      </div>

      <div class="edit-grid">
        <label class="edit-field">
          <span class="edit-label">Title</span>
          <input class="input edit-title" type="text" value="${escape(card.title)}" />
        </label>

        <label class="edit-field">
          <span class="edit-label">Category</span>
          <input class="input edit-category" type="text" value="${escape(card.category)}" />
        </label>

        <label class="edit-field">
          <span class="edit-label">Context</span>
          <textarea class="textarea edit-context" rows="6">${escape(card.context)}</textarea>
        </label>

        <label class="edit-field">
          <span class="edit-label">Question</span>
          <textarea class="textarea edit-question" rows="3">${escape(card.question)}</textarea>
        </label>

        ${
          isSelect
            ? `<label class="edit-field">
                 <span class="edit-label">Options (one per line)</span>
                 <textarea class="textarea edit-options" rows="${Math.max(4, (card.options?.length ?? 0) + 1)}">${escape(optionsText)}</textarea>
               </label>`
            : ""
        }

        <label class="edit-field">
          <span class="edit-label">Active reference path (optional)</span>
          <input class="input edit-attachment" type="text" placeholder="deliverables/example.html" value="${escape(card.attachment_path ?? "")}" />
        </label>

        <label class="edit-toggle">
          <input class="edit-skip" type="checkbox" ${card.skip_allowed ? "checked" : ""} />
          <span>Skip allowed</span>
        </label>
      </div>

      <div class="edit-actions">
        <button class="btn-primary-sm" type="button" data-action="edit-card-save">Save changes</button>
        <button class="btn-ghost-sm" type="button" data-action="edit-card-cancel">Cancel</button>
      </div>
    </article>
  `;
}

function labelFor(response: ClientResponse | undefined): string {
  if (!response) return "Not viewed";
  switch (response.state) {
    case "answered": return "Answered";
    case "skipped": return "Skipped";
    case "viewed": return "Viewed";
    case "needs_edit": return "Editing";
    case "not_started":
    default: return "Not viewed";
  }
}

function stateClassFor(response: ClientResponse | undefined): string {
  if (!response) return "state-pending";
  switch (response.state) {
    case "answered": return "state-answered";
    case "skipped": return "state-skipped";
    case "viewed": return "state-viewed";
    default: return "state-pending";
  }
}

function responseBodyMutedClass(response: ClientResponse | undefined): string {
  if (!response) return " muted";
  if (response.state === "viewed" || response.state === "skipped" || response.state === "not_started") return " muted";
  return "";
}

interface ResponseValueShape {
  confirmed?: boolean;
  correction?: string;
  selected?: string | string[];
  text?: string;
  url?: string;
  name?: string;
  email?: string;
  role?: string;
  file_ids?: string[];
  note?: string;
}

function renderResponseBodyHtml(card: Card, response: ClientResponse | undefined, uploads: UploadRow[]): string {
  if (!response || response.state === "not_started") return "Not yet viewed.";
  if (response.state === "viewed") return "Card opened, no response yet.";

  const v = (response.response_value ?? {}) as ResponseValueShape;
  const noteHtml = v.note ? `<div class="response-note"><strong>Note:</strong> ${escape(v.note)}</div>` : "";

  if (response.state === "skipped") return `Skipped.${noteHtml}`;

  let body: string;
  switch (card.response_type) {
    case "confirm-edit":
      body = v.confirmed ? "Confirmed as written." : `Edited:\n${v.correction ?? ""}`;
      break;
    case "single-select":
      body = escape(String(v.selected ?? ""));
      break;
    case "multi-select": {
      const arr = Array.isArray(v.selected) ? v.selected : [];
      body = arr.length === 0 ? "None selected." : `<ul>${arr.map((s) => `<li>${escape(s)}</li>`).join("")}</ul>`;
      break;
    }
    case "short-text":
    case "long-text":
      body = escape(v.text ?? "");
      break;
    case "document-link":
      body = v.url ? `<a href="${escape(v.url)}" target="_blank" rel="noreferrer noopener">${escape(v.url)}</a>` : "";
      break;
    case "contact-share":
      body = [
        v.name ? `<strong>${escape(v.name)}</strong>` : "",
        v.role ? ` (${escape(v.role)})` : "",
        v.email ? `\n${escape(v.email)}` : "",
      ].join("");
      break;
    case "file-upload":
      body =
        uploads.length === 0
          ? "No files uploaded."
          : `<ul class="uploads-list">${uploads
              .map((u) => {
                const url = adminApi.uploadDownloadUrl(u.id);
                const label = `${escape(u.file_name)} <span class="upload-size">(${formatBytes(u.file_size_bytes)})</span>`;
                return `<li><a href="${escape(url)}" target="_blank" rel="noreferrer noopener" class="upload-link">${label}</a></li>`;
              })
              .join("")}</ul>`;
      break;
    default:
      body = "";
  }
  return body + noteHtml;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── markdown export plumbing ─────────────────────────────────────────────

function summarizeUploads(uploads: UploadRow[]): UploadInfo[] {
  return uploads.map((u) => ({ id: u.id, name: u.file_name, sizeBytes: u.file_size_bytes }));
}

function buildSingleCardMarkdown(
  client: Client,
  card: Card,
  response: ClientResponse | undefined,
  uploads: UploadRow[],
  statusOverride: Status | undefined
): string {
  const status = statusOverride ?? suggestStatus(card, response);
  return renderCardMarkdown({ card, client, response, status, uploads: summarizeUploads(uploads) });
}

function buildEngagementMarkdown(data: DetailViewData, overrides: Map<string, Status>): string {
  const blocks: string[] = [];
  for (const card of data.cards) {
    const response = data.responses.get(card.id);
    const status = overrides.get(card.id) ?? suggestStatus(card, response);
    blocks.push(
      renderCardMarkdown({
        card,
        client: data.client,
        response,
        status,
        uploads: summarizeUploads(data.uploads.get(card.id) ?? []),
      })
    );
  }
  return renderEngagementMarkdown(blocks);
}

function slugify(s: string): string {
  return s
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function downloadFilename(client: Client): string {
  const today = new Date().toISOString().slice(0, 10);
  const parts = [client.org_name, client.engagement_name]
    .map((s) => s?.trim())
    .filter((s): s is string => !!s)
    .map(slugify);
  const stem = parts.length > 0 ? parts.join("-") : slugify(client.name);
  return `${stem}-${today}.md`;
}

function triggerDownload(text: string, filename: string): void {
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ── tiny UI helpers ──────────────────────────────────────────────────────

let activeToast: HTMLElement | null = null;
function toast(msg: string): void {
  if (activeToast) activeToast.remove();
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  activeToast = el;
  setTimeout(() => {
    if (activeToast === el) {
      el.remove();
      activeToast = null;
    }
  }, 1800);
}

function flashCopied(btn: HTMLButtonElement, label: string): void {
  const original = btn.textContent ?? "";
  btn.textContent = label;
  btn.classList.add("copied-flash");
  setTimeout(() => {
    btn.textContent = original;
    btn.classList.remove("copied-flash");
  }, 1200);
}

if (import.meta.hot) import.meta.hot.decline();

main().catch((err) => {
  console.error("Pulse admin failed:", err);
});

void BASE_URL;
