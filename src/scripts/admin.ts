import {
  adminApi,
  ApiError,
  authApi,
  groupsApi,
  orgsApi,
  type AuthUser,
  type Card,
  type ClientResponse,
  type Engagement,
  type EngagementDetail,
  type EngagementSummary,
  type GroupSummary,
  type OrgDetails,
  type OrgSummary,
  type ResponseType,
  type UpdateEngagementArgs,
  type UploadRow,
} from "../lib/api";
import { applyBranding } from "../lib/branding";
import { formatTimestamp } from "../lib/format-time";
import { renderOrgSwitcher } from "./org-switcher";
import {
  renderSettings as renderSettingsPage,
  type SettingsTab,
} from "./settings";
import { renderSuperadmin } from "./superadmin";
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

/**
 * Return a SAFE same-origin `return_to` target from the current URL, or
 * null. Used to bounce the operator back to the OAuth consent page after
 * a successful sign-in. Rejects anything that isn't strictly same-origin
 * (absolute off-origin URLs, protocol-relative `//evil.com`, etc.) so a
 * crafted link can never redirect the browser off Pulse.
 */
function safeReturnTo(): string | null {
  const raw = new URLSearchParams(window.location.search).get("return_to");
  if (!raw) return null;
  // Resolve against our own origin; a same-origin result is the only one
  // we accept. `new URL(raw, origin)` resolves `/path` against origin and
  // leaves absolute URLs (incl. `//host`) at their own origin.
  let resolved: URL;
  try {
    resolved = new URL(raw, window.location.origin);
  } catch {
    return null;
  }
  if (resolved.origin !== window.location.origin) return null;
  return resolved.pathname + resolved.search + resolved.hash;
}

/**
 * If a valid same-origin `return_to` is present, navigate there and
 * return true (caller should stop). Otherwise return false so the normal
 * admin shell renders.
 */
function honorReturnTo(): boolean {
  const target = safeReturnTo();
  if (!target) return false;
  window.location.assign(target);
  return true;
}

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

  // OAuth callbacks may include `?error=invitation_required` when an
  // unknown email signs in via Google/Microsoft without a pending invite.
  // Render an actionable message instead of a bare login form.
  const errParam = params.get("error");
  if (errParam === "invitation_required") {
    renderLogin(
      mount,
      "You need an invitation to use Pulse. Ask an org owner to invite you, then click the link in the email.",
    );
    // Strip the query param so a refresh doesn't repeat the message.
    const url = new URL(window.location.href);
    url.searchParams.delete("error");
    window.history.replaceState({}, "", url.toString());
    return;
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

  // Post multi-tenant migration: admin access is gated by org membership.
  // A signed-in user with no active org cannot reach any `/api/admin/*`
  // route; show a friendlier message than the bare 403 the API would
  // return on the first protected call.
  if (!me.active_org_id) {
    renderLogin(
      mount,
      "Your account isn't a member of any organization yet. Ask an org owner to invite you.",
    );
    return;
  }

  // Already authenticated (incl. landing back from an OAuth callback):
  // if a same-origin `return_to` is present (e.g. the OAuth consent
  // page), bounce there instead of rendering the admin shell.
  if (honorReturnTo()) return;

  void runAdmin(mount, me);
}

// ── login + signup + email flows ─────────────────────────────────────────

function renderLogin(mount: HTMLElement, errorMsg?: string): void {
  mount.innerHTML = `
    <form class="login-card" id="login-form" novalidate>
      <h1>Pulse admin</h1>
      <p>Sign in to manage engagements.</p>
      ${errorMsg ? `<div class="login-error">${escape(errorMsg)}</div>` : ""}

      <!--
        Google sign-in button per Google Identity Branding Guidelines
        (https://developers.google.com/identity/branding-guidelines).
        Microsoft OAuth is still wired on the backend (/api/auth/microsoft/*)
        for future re-enable; the entry point is intentionally hidden here.
      -->
      <a class="btn-google" href="${escape(authApi.oauthAuthorizeUrl("google"))}" aria-label="Sign in with Google">
        <span class="btn-google-icon" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg" focusable="false">
            <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z" fill="#4285F4"/>
            <path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332C2.438 15.983 5.482 18 9 18z" fill="#34A853"/>
            <path d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" fill="#FBBC05"/>
            <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0 5.482 0 2.438 2.017.957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
          </svg>
        </span>
        <span class="btn-google-label">Sign in with Google</span>
      </a>

      <div class="login-divider" role="separator" aria-orientation="horizontal"><span>or</span></div>

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
      if (!user.active_org_id) {
        renderLogin(
          mount,
          "Your account isn't a member of any organization yet. Ask an org owner to invite you.",
        );
        return;
      }
      // Honor a same-origin `return_to` (the OAuth consent page) over the
      // normal admin landing.
      if (honorReturnTo()) return;
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
  engagementId: string;
}
interface RouteSettings {
  kind: "settings";
  tab: SettingsTab;
}
interface RouteSuperadmin {
  kind: "superadmin";
}
type Route = RouteList | RouteDetail | RouteSettings | RouteSuperadmin;

function parseRoute(): Route {
  const hash = window.location.hash.replace(/^#/, "");
  const m = hash.match(/^client\/([0-9a-f-]+)$/i);
  if (m) return { kind: "detail", engagementId: m[1] };
  // Bare `#settings` redirects to `#settings/personal`; the explicit
  // `#settings/organization` and `#settings/activity` paths open the
  // matching tab. Anything else we treat as Personal so a typo doesn't
  // dead-end the user.
  if (hash === "settings/organization") {
    return { kind: "settings", tab: "organization" };
  }
  if (hash === "settings/activity") {
    return { kind: "settings", tab: "activity" };
  }
  if (hash === "settings" || hash.startsWith("settings/")) {
    return { kind: "settings", tab: "personal" };
  }
  if (hash === "superadmin") {
    return { kind: "superadmin" };
  }
  return { kind: "list" };
}

// Shared state for the admin shell — orgs (for the switcher) and the
// active org's details (logo + name). Fetched once on boot and refreshed
// whenever the user switches or changes org settings.
interface ShellState {
  user: AuthUser;
  orgs: OrgSummary[];
  activeOrg: OrgDetails;
}

async function loadShellState(user: AuthUser): Promise<ShellState> {
  const [orgs, activeOrg] = await Promise.all([
    orgsApi.listMine(),
    orgsApi.me(),
  ]);
  // Apply the active org's theme to the live document. Every refresh
  // (boot, org switch, post-settings reload) routes through here, so the
  // console re-themes consistently — and an org with no custom branding
  // resets cleanly to the Axiolo defaults.
  applyBranding(activeOrg.branding);
  return { user, orgs, activeOrg };
}

async function runAdmin(mount: HTMLElement, user: AuthUser): Promise<void> {
  // Normalize a bare `#settings` hash to `#settings/personal` once on
  // boot so the back button skips the redirect step instead of
  // ping-ponging between the two.
  if (window.location.hash === "#settings") {
    window.history.replaceState({}, "", "#settings/personal");
  }

  let state: ShellState;
  try {
    state = await loadShellState(user);
  } catch (err) {
    if (
      err instanceof ApiError &&
      (err.status === 401 || err.status === 403)
    ) {
      renderLogin(mount, "Your session expired. Please sign in again.");
      return;
    }
    throw err;
  }

  mount.innerHTML = renderShell(state.user);
  attachShellHandlers(mount, state);
  renderShellOrg(mount, state);

  const container = mount.querySelector<HTMLElement>(".admin-container")!;

  let route = parseRoute();
  setActiveNav(mount, route);
  await draw(container, route, state);

  window.addEventListener("hashchange", async () => {
    if (window.location.hash === "#settings") {
      window.history.replaceState({}, "", "#settings/personal");
    }
    route = parseRoute();
    setActiveNav(mount, route);
    await draw(container, route, state);
  });
}

function renderShell(user: AuthUser): string {
  const baseSlash = BASE_URL.endsWith("/") ? BASE_URL : `${BASE_URL}/`;
  // Superadmin link only renders for users with `is_superadmin = true`.
  // The backend is still the source of truth — `get_current_superadmin`
  // gates every `/api/superadmin/*` call independently — but hiding the
  // nav entry keeps the page from misleading non-super users into
  // clicking through to a route they'd 404 on.
  const superLink = user.is_superadmin
    ? `<a class="admin-header-link" href="#superadmin" id="nav-superadmin">Superadmin</a>`
    : "";
  return `
    <div class="admin-page">
      <header class="admin-header" role="banner">
        <div class="admin-header-brand">
          <span class="brand">
            <img src="${escape(baseSlash)}axiolo-logo.svg" alt="Axiolo" class="brand-logo" width="84" height="23" />
            <span class="brand-sep" aria-hidden="true">·</span>
            Pulse
            <span class="admin-title" style="margin-left:8px">Admin</span>
          </span>
          <div class="admin-header-org" id="admin-header-org" aria-label="Active organization"></div>
        </div>
        <nav class="admin-header-actions" aria-label="Primary">
          <a class="admin-header-link" href="#" id="nav-engagements">Engagements</a>
          <a class="admin-header-link" href="#settings/personal" id="nav-settings">Settings</a>
          ${superLink}
          <button class="admin-logout" type="button" id="logout">Sign out</button>
        </nav>
      </header>
      <div class="admin-container">
        <div class="loading">Loading...</div>
      </div>
    </div>
  `;
}

function attachShellHandlers(mount: HTMLElement, state: ShellState): void {
  mount
    .querySelector<HTMLButtonElement>("#logout")
    ?.addEventListener("click", async () => {
      try {
        await authApi.logout();
      } catch {
        // ignore — best-effort
      }
      window.location.hash = "";
      renderLogin(mount);
    });
  mount
    .querySelector<HTMLAnchorElement>("#nav-engagements")
    ?.addEventListener("click", (e) => {
      e.preventDefault();
      window.location.hash = "";
    });
  void state;
}

function renderShellOrg(mount: HTMLElement, state: ShellState): void {
  const slot = mount.querySelector<HTMLElement>("#admin-header-org");
  if (!slot) return;
  renderOrgSwitcher(slot, {
    orgs: state.orgs,
    activeOrgId: state.activeOrg.id,
    onSwitch: async (orgId) => {
      try {
        await orgsApi.switchOrg(orgId);
      } catch (err) {
        console.error("switch org:", err);
        const detail =
          err instanceof ApiError ? err.detail : "Could not switch";
        toast(detail);
        return;
      }
      // Reload everything: the engagement list, the active org details,
      // the switcher. Easiest correct way is to re-run `loadShellState`
      // and then re-render the current route.
      const refreshed = await loadShellState(state.user);
      state.orgs = refreshed.orgs;
      state.activeOrg = refreshed.activeOrg;
      renderShellOrg(mount, state);
      const container = mount.querySelector<HTMLElement>(".admin-container");
      if (container) {
        await draw(container, parseRoute(), state);
      }
      toast(`Switched to ${state.activeOrg.name}`);
    },
  });
}

function setActiveNav(mount: HTMLElement, route: Route): void {
  const setActive = (id: string, active: boolean) => {
    mount.querySelector<HTMLElement>(`#${id}`)?.classList.toggle("active", active);
  };
  setActive("nav-engagements", route.kind === "list" || route.kind === "detail");
  setActive("nav-settings", route.kind === "settings");
  setActive("nav-superadmin", route.kind === "superadmin");
}

async function draw(
  container: HTMLElement,
  route: Route,
  state: ShellState,
): Promise<void> {
  if (route.kind === "list") {
    container.innerHTML = `<div class="loading">Loading engagements...</div>`;
    try {
      const [summaries, groups] = await Promise.all([
        adminApi.listEngagements(),
        groupsApi.list(),
      ]);
      renderList(container, summaries, groups);
    } catch (err) {
      console.error("load engagements:", err);
      container.innerHTML = `<div class="error"><h1 class="error-title">Could not load</h1><p class="error-body">Please refresh.</p></div>`;
    }
    return;
  }

  if (route.kind === "superadmin") {
    container.innerHTML = `<div class="loading">Loading superadmin tools...</div>`;
    try {
      await renderSuperadmin({
        container,
        user: state.user,
        helpers: { toast },
      });
      // The "back to engagements" CTA inside the not-found state needs
      // the same hash-clearing handler the other empty states use.
      container
        .querySelector<HTMLAnchorElement>("[data-go-home]")
        ?.addEventListener("click", (e) => {
          e.preventDefault();
          window.location.hash = "";
        });
    } catch (err) {
      console.error("load superadmin:", err);
      container.innerHTML = `<div class="error"><h1 class="error-title">Could not load</h1><p class="error-body">Please refresh.</p></div>`;
    }
    return;
  }

  if (route.kind === "settings") {
    container.innerHTML = `<div class="loading">Loading settings...</div>`;
    try {
      // Fetch in parallel. The invites list returns [] for non-owners,
      // but the endpoint is open to any member of the org, so we can
      // always call it — owner-gating happens at the UI layer.
      const [me, identities, apiKeys, org, members, invites] =
        await Promise.all([
          authApi.me(),
          authApi.listIdentities(),
          authApi.listApiKeys(),
          orgsApi.me(),
          orgsApi.listMembers(),
          orgsApi.listInvites().catch((err) => {
            // A 403 here would mean the user is somehow scoped to an
            // org they don't belong to; surface an empty list rather
            // than crashing the whole settings page.
            if (err instanceof ApiError && err.status === 403) return [];
            throw err;
          }),
        ]);
      // Keep ShellState's activeOrg in sync after settings re-fetches it.
      state.activeOrg = org;
      const mountRoot = document.getElementById("admin");
      renderSettingsPage({
        container,
        tab: route.tab,
        user: me,
        org,
        identities,
        apiKeys,
        members,
        invites,
        helpers: {
          toast,
          confirm: openConfirmModal,
          onOrgChanged: async () => {
            // Refresh the switcher + header logo without re-running
            // the entire settings page draw.
            try {
              const fresh = await loadShellState(state.user);
              state.orgs = fresh.orgs;
              state.activeOrg = fresh.activeOrg;
              if (mountRoot) renderShellOrg(mountRoot, state);
            } catch (err) {
              console.warn("refresh shell after org change:", err);
            }
          },
        },
      });
    } catch (err) {
      console.error("load settings:", err);
      container.innerHTML = `<div class="error"><h1 class="error-title">Could not load</h1><p class="error-body">Please refresh.</p></div>`;
    }
    return;
  }

  container.innerHTML = `<div class="loading">Loading responses...</div>`;
  try {
    const detail = await adminApi.getEngagement(route.engagementId);
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

// ── settings view ──
// The settings page (Personal + Organization tabs) lives in
// `./settings.ts`. The admin shell loads `orgsApi.me()` + members +
// invites alongside the personal data and routes both tabs through
// `renderSettingsPage`.

// ── list view ───────────────────────────────────────────────────────────

/** Synthetic group id for the implicit "Ungrouped" bucket. Engagements
 * with `group_id === null` collect here; it's never sent to the backend
 * as an id (the bucket is just `null` on the wire). */
const UNGROUPED = "__ungrouped__";

/** Build the `<option>` list for a "move to folder" / modal folder
 * select. `selectedId` (null → Ungrouped) marks the current folder. */
function folderOptionsHtml(
  groups: GroupSummary[],
  selectedId: string | null,
): string {
  const ungroupedSel = selectedId === null ? " selected" : "";
  const opts = groups
    .map(
      (g) =>
        `<option value="${escape(g.id)}"${selectedId === g.id ? " selected" : ""}>${escape(g.name)}</option>`,
    )
    .join("");
  return `<option value="${UNGROUPED}"${ungroupedSel}>Ungrouped</option>${opts}`;
}

function engagementRowHtml(
  s: EngagementSummary,
  groups: GroupSummary[],
): string {
  const completed = s.answered_count + s.skipped_count;
  return `
    <tr data-engagement-id="${escape(s.id)}">
      <td>
        <div class="client-name">${escape(s.name)}</div>
        <div class="org-name">${escape(s.org_name ?? "")}</div>
      </td>
      <td>${escape(s.engagement_name ?? "")}</td>
      <td>
        <span class="progress-pill">${completed} / ${s.total_cards}</span>
      </td>
      <td class="last-active">${escape(formatTimestamp(s.last_active_at))}</td>
      <td class="move-cell">
        <label class="move-folder">
          <span class="sr-only">Move to folder</span>
          <select class="input move-folder-select" data-action="move" aria-label="Move ${escape(s.name)} to folder">
            ${folderOptionsHtml(groups, s.group_id)}
          </select>
        </label>
      </td>
      <td class="actions">
        <div class="action-icons">
          <button class="action-icon" type="button" data-action="view" aria-label="View responses" title="View responses">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
          </button>
          <button class="action-icon" type="button" data-action="copy-link" aria-label="Copy link" title="Copy link">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
          </button>
          <button class="action-icon danger" type="button" data-action="delete" aria-label="Delete" title="Delete">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
          </button>
        </div>
      </td>
    </tr>`;
}

function folderSectionHtml(
  heading: string,
  groupId: string | null,
  rowsHtml: string,
  count: number,
  controls: boolean,
): string {
  const groupAttr = groupId === null ? UNGROUPED : escape(groupId);
  const folderControls = controls
    ? `
        <button class="action-link" type="button" data-folder-action="rename">Rename</button>
        <button class="action-link danger" type="button" data-folder-action="delete">Delete folder</button>`
    : "";
  const body = rowsHtml
    ? `
      <div class="engagement-table-wrap">
        <table class="engagement-table">
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>`
    : `<div class="folder-empty">No engagements in this folder yet.</div>`;
  return `
    <section class="folder-section" data-group-id="${groupAttr}">
      <div class="folder-header">
        <h3 class="folder-name">${escape(heading)} <span class="folder-count">${count}</span></h3>
        <div class="folder-actions">${folderControls}</div>
      </div>
      ${body}
    </section>`;
}

function renderList(
  container: HTMLElement,
  summaries: EngagementSummary[],
  groups: GroupSummary[],
): void {
  const header = `
    <div class="engagement-list-header">
      <h2 class="engagement-list-h">Engagements</h2>
      <div class="engagement-list-actions">
        <button class="btn-secondary-sm" type="button" data-action="new-folder">+ New folder</button>
        <button class="btn-primary-sm" type="button" data-action="new-engagement">+ New engagement</button>
      </div>
    </div>
  `;

  if (summaries.length === 0 && groups.length === 0) {
    container.innerHTML = `
      ${header}
      <div class="empty-card">
        <p>No engagements yet. Click + New engagement to create your first one.</p>
      </div>
    `;
    bindListHeader(container, groups);
    return;
  }

  // Bucket engagements by folder. Server already orders by created_at
  // desc, so iterating `summaries` keeps that order within each bucket.
  const byGroup = new Map<string, EngagementSummary[]>();
  const ungrouped: EngagementSummary[] = [];
  for (const s of summaries) {
    if (s.group_id === null) {
      ungrouped.push(s);
    } else {
      const list = byGroup.get(s.group_id) ?? [];
      list.push(s);
      byGroup.set(s.group_id, list);
    }
  }

  // Folder sections (alphabetical — the API returns them ordered by
  // name), then the Ungrouped bucket last. Empty folders still render.
  const sections = groups
    .map((g) => {
      const members = byGroup.get(g.id) ?? [];
      const rowsHtml = members.map((s) => engagementRowHtml(s, groups)).join("");
      return folderSectionHtml(g.name, g.id, rowsHtml, members.length, true);
    })
    .join("");

  const ungroupedHtml =
    ungrouped.length > 0 || groups.length > 0
      ? folderSectionHtml(
          "Ungrouped",
          null,
          ungrouped.map((s) => engagementRowHtml(s, groups)).join(""),
          ungrouped.length,
          false,
        )
      : "";

  container.innerHTML = `
    ${header}
    <div class="folder-list">${sections}${ungroupedHtml}</div>
  `;

  bindListHeader(container, groups);

  // ── select change → move engagement to a folder ──
  container.addEventListener("change", async (e) => {
    const target = e.target;
    if (!(target instanceof HTMLSelectElement)) return;
    if (target.dataset.action !== "move") return;
    const row = target.closest<HTMLElement>("tr[data-engagement-id]");
    if (!row) return;
    const engagementId = row.dataset.engagementId!;
    const raw = target.value;
    const newGroupId = raw === UNGROUPED ? null : raw;
    try {
      await adminApi.updateEngagement(engagementId, { group_id: newGroupId });
      toast(newGroupId === null ? "Moved to Ungrouped" : "Moved to folder");
      await reloadList(container);
    } catch (err) {
      console.error("move engagement:", err);
      toast("Could not move engagement");
      await reloadList(container);
    }
  });

  // ── folder rename / delete ──
  container.addEventListener("click", async (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    const folderBtn = target.closest<HTMLButtonElement>("[data-folder-action]");
    if (!folderBtn) return;
    const section = folderBtn.closest<HTMLElement>(".folder-section");
    const groupId = section?.dataset.groupId;
    if (!groupId || groupId === UNGROUPED) return;
    const group = groups.find((g) => g.id === groupId);
    if (!group) return;

    if (folderBtn.dataset.folderAction === "rename") {
      openRenameFolderModal(container, group);
      return;
    }
    if (folderBtn.dataset.folderAction === "delete") {
      const n = group.client_count;
      openConfirmModal({
        title: "Delete folder",
        body: [
          `Delete the folder "${group.name}"?`,
          n > 0
            ? `Its ${n} engagement${n === 1 ? "" : "s"} will move to Ungrouped — they are NOT deleted.`
            : "This folder is empty.",
        ].join("\n"),
        confirmLabel: "Delete folder",
        danger: true,
        onConfirm: async () => {
          await groupsApi.delete(group.id);
          toast("Folder deleted");
          await reloadList(container);
        },
      });
      return;
    }
  });

  // ── engagement row actions (view / copy / delete) ──
  container.addEventListener("click", async (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "new-engagement" || action === "new-folder") return; // handled in bindListHeader

    const row = btn.closest<HTMLElement>("tr[data-engagement-id]");
    if (!row) return;
    const engagementId = row.dataset.engagementId!;

    const summary = summaries.find((s) => s.id === engagementId);
    if (!summary) return;

    switch (action) {
      case "view":
        window.location.hash = `client/${engagementId}`;
        return;
      case "copy-link":
        await navigator.clipboard.writeText(`${PROD_URL}?t=${summary.token}`);
        toast("Link copied to clipboard");
        return;
      case "delete": {
        const label = [summary.name, summary.engagement_name].filter(Boolean).join(" · ");
        const totalCards = summary.total_cards;
        const completed = summary.answered_count + summary.skipped_count;
        const body = [
          `Delete ${label}?`,
          totalCards > 0
            ? `This will permanently remove ${totalCards} card${totalCards === 1 ? "" : "s"} and ${completed} response${completed === 1 ? "" : "s"}, plus any uploaded files.`
            : "No cards have been added to this engagement yet.",
          "This cannot be undone.",
        ].join("\n");
        openConfirmModal({
          title: "Delete engagement",
          body,
          confirmLabel: "Delete",
          danger: true,
          onConfirm: async () => {
            await adminApi.deleteEngagement(summary.id);
            toast("Engagement deleted");
            await reloadList(container);
          },
        });
        return;
      }
    }
  });
}

/** Wire the list header's "New folder" + "New engagement" buttons.
 * Factored out so the empty-state and populated paths share it. */
function bindListHeader(container: HTMLElement, groups: GroupSummary[]): void {
  container
    .querySelector<HTMLButtonElement>("[data-action='new-engagement']")
    ?.addEventListener("click", () => openNewEngagementModal(container, groups));
  container
    .querySelector<HTMLButtonElement>("[data-action='new-folder']")
    ?.addEventListener("click", () => openNewFolderModal(container));
}

// ── folder modals ────────────────────────────────────────────────────────

function openNewFolderModal(container: HTMLElement): void {
  openFolderNameModal({
    title: "New folder",
    label: "Folder name",
    initial: "",
    submitLabel: "Create folder",
    onSubmit: async (name) => {
      await groupsApi.create(name);
      toast("Folder created");
      await reloadList(container);
    },
  });
}

function openRenameFolderModal(
  container: HTMLElement,
  group: GroupSummary,
): void {
  openFolderNameModal({
    title: "Rename folder",
    label: "Folder name",
    initial: group.name,
    submitLabel: "Save",
    onSubmit: async (name) => {
      await groupsApi.rename(group.id, name);
      toast("Folder renamed");
      await reloadList(container);
    },
  });
}

interface FolderNameModalOptions {
  title: string;
  label: string;
  initial: string;
  submitLabel: string;
  onSubmit: (name: string) => Promise<void>;
}

function openFolderNameModal(opts: FolderNameModalOptions): void {
  const modalEl = document.createElement("div");
  modalEl.className = "modal";
  modalEl.innerHTML = `
    <div class="modal-backdrop" data-close></div>
    <div class="modal-panel new-eng-panel">
      <header class="modal-header">
        <span class="modal-title">${escape(opts.title)}</span>
        <button class="modal-close" type="button" data-close aria-label="Close">×</button>
      </header>
      <form class="new-eng-form" id="folder-name-form">
        <label class="edit-field">
          <span class="edit-label">${escape(opts.label)}</span>
          <input class="input" id="folder-name" type="text" autofocus required value="${escape(opts.initial)}" />
        </label>
        <div class="edit-actions">
          <button class="btn-primary-sm" type="submit">${escape(opts.submitLabel)}</button>
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

  modalEl
    .querySelector<HTMLFormElement>("#folder-name-form")!
    .addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = (
        modalEl.querySelector<HTMLInputElement>("#folder-name")?.value ?? ""
      ).trim();
      if (!name) {
        modalEl.querySelector<HTMLInputElement>("#folder-name")?.focus();
        return;
      }
      const submitBtn = modalEl.querySelector<HTMLButtonElement>("button[type='submit']");
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "Working...";
      }
      try {
        await opts.onSubmit(name);
        close();
      } catch (err) {
        console.error("folder name modal:", err);
        toast("Could not save folder");
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = opts.submitLabel;
        }
      }
    });
}

// ── new engagement modal ────────────────────────────────────────────────

function openNewEngagementModal(
  container: HTMLElement,
  groups: GroupSummary[],
): void {
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
        <label class="edit-field">
          <span class="edit-label">Folder (optional)</span>
          <select class="input" id="ne-folder">
            ${folderOptionsHtml(groups, null)}
          </select>
        </label>
        <label class="edit-field edit-field--check">
          <input type="checkbox" id="ne-voice" />
          <span class="edit-label">Voice recording</span>
        </label>
        <p class="edit-hint">Let clients record spoken answers on this engagement's questions.</p>
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
    const folderRaw = modalEl.querySelector<HTMLSelectElement>("#ne-folder")?.value ?? UNGROUPED;
    const groupId = folderRaw === UNGROUPED ? null : folderRaw;
    const voiceEnabled = modalEl.querySelector<HTMLInputElement>("#ne-voice")?.checked ?? false;
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
      const created = await adminApi.createEngagement({
        name,
        org_name: org || null,
        engagement_name: eng || null,
      });
      // Create accepts neither group_id nor voice_enabled — both are
      // assigned via PATCH. Fold them into a single follow-up update so a
      // new engagement lands in the right folder with voice on if asked.
      // Voice defaults off server-side, so only PATCH when it's checked.
      const patch: UpdateEngagementArgs = {};
      if (groupId !== null) patch.group_id = groupId;
      if (voiceEnabled) patch.voice_enabled = true;
      if (Object.keys(patch).length > 0) {
        await adminApi.updateEngagement(created.id, patch);
      }
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

function renderDetailHeader(client: Engagement): string {
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
      <button class="btn-secondary-sm warn" type="button" id="reset-engagement">Reset answers</button>
      <button class="btn-secondary-sm danger" type="button" id="delete-engagement">Delete</button>
    </div>
  `;
}

interface ConfirmModalOptions {
  title: string;
  body: string; // plain text, multi-line allowed
  confirmLabel: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void | Promise<void>;
}

function openConfirmModal(opts: ConfirmModalOptions): void {
  // Reentrancy guard: if the user clicks the trigger twice (or a click
  // handler fires twice), don't stack confirms — the second open would
  // have stale state and create an orphan when the first closes.
  document.body.querySelectorAll<HTMLElement>(".modal.confirm-modal").forEach((el) => el.remove());

  const modalEl = document.createElement("div");
  modalEl.className = "modal confirm-modal";
  const bodyHtml = opts.body
    .split("\n")
    .map((line) => `<p class="confirm-body-line">${escape(line)}</p>`)
    .join("");
  modalEl.innerHTML = `
    <div class="modal-backdrop" data-close></div>
    <div class="modal-panel confirm-panel">
      <header class="modal-header">
        <span class="modal-title">${escape(opts.title)}</span>
        <button class="modal-close" type="button" data-close aria-label="Close">×</button>
      </header>
      <div class="confirm-body">${bodyHtml}</div>
      <div class="confirm-actions">
        <button class="btn-ghost-sm" type="button" data-close>${escape(opts.cancelLabel ?? "Cancel")}</button>
        <button class="${opts.danger ? "btn-danger-sm" : "btn-primary-sm"}" type="button" data-confirm>${escape(opts.confirmLabel)}</button>
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

  const confirmBtn = modalEl.querySelector<HTMLButtonElement>("[data-confirm]")!;
  confirmBtn.addEventListener("click", async () => {
    confirmBtn.disabled = true;
    const original = confirmBtn.textContent;
    confirmBtn.textContent = "Working...";
    try {
      await opts.onConfirm();
      close();
    } catch (err) {
      console.error("confirm action:", err);
      confirmBtn.disabled = false;
      confirmBtn.textContent = original;
    }
  });

  confirmBtn.focus();
}

function openEditEngagementModal(
  client: Engagement & { token: string },
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
        <label class="edit-field">
          <span class="edit-label">Folder (optional)</span>
          <select class="input" id="ee-folder" disabled>
            <option value="${UNGROUPED}" selected>Loading folders…</option>
          </select>
        </label>
        <label class="edit-field edit-field--check">
          <input type="checkbox" id="ee-voice" ${client.voice_enabled ? "checked" : ""} />
          <span class="edit-label">Voice recording</span>
        </label>
        <p class="edit-hint">Let clients record spoken answers on this engagement's questions.</p>
        <div class="edit-actions">
          <button class="btn-primary-sm" type="submit">Save changes</button>
          <button class="btn-ghost-sm" type="button" data-close>Cancel</button>
        </div>
      </form>
    </div>
  `;
  document.body.appendChild(modalEl);

  const folderSelect = modalEl.querySelector<HTMLSelectElement>("#ee-folder")!;
  // Populate the folder dropdown asynchronously; the rest of the form is
  // usable immediately. If the fetch fails the select stays disabled and
  // the save simply omits group_id (leaves the folder untouched).
  let foldersLoaded = false;
  void groupsApi
    .list()
    .then((groups) => {
      folderSelect.innerHTML = folderOptionsHtml(groups, client.group_id ?? null);
      folderSelect.disabled = false;
      foldersLoaded = true;
    })
    .catch((err) => {
      console.error("load folders for edit:", err);
      folderSelect.innerHTML = `<option value="${UNGROUPED}" selected>Folders unavailable</option>`;
    });

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

    const voiceEnabled = modalEl.querySelector<HTMLInputElement>("#ee-voice")?.checked ?? false;
    const args: UpdateEngagementArgs = {
      name,
      org_name: org || null,
      engagement_name: eng || null,
      voice_enabled: voiceEnabled,
    };
    // Only send group_id once the folder list loaded — otherwise we'd
    // send the placeholder value and accidentally ungroup the engagement.
    if (foldersLoaded) {
      const raw = folderSelect.value;
      args.group_id = raw === UNGROUPED ? null : raw;
    }

    try {
      const updated = await adminApi.updateEngagement(client.id, args);
      client.group_id = updated.group_id ?? null;
      client.voice_enabled = updated.voice_enabled;
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

// Re-fetch + re-render the engagement list in place. Used by handlers
// that mutate the list (delete) and don't have a
// `ShellState` handle to pass to `draw`. The list view is self-contained
// — it doesn't need org switcher state because the shell header is
// untouched.
async function reloadList(container: HTMLElement): Promise<void> {
  try {
    const [summaries, groups] = await Promise.all([
      adminApi.listEngagements(),
      groupsApi.list(),
    ]);
    renderList(container, summaries, groups);
  } catch (err) {
    console.error("reload list:", err);
    container.innerHTML = `<div class="error"><h1 class="error-title">Could not load</h1><p class="error-body">Please refresh.</p></div>`;
  }
}

// ── detail view ──────────────────────────────────────────────────────────

interface DetailViewData {
  client: Engagement & { token: string };
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
  return { client: payload.engagement, cards: payload.cards, responses, uploads };
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

    headerEl.querySelector<HTMLButtonElement>("#delete-engagement")?.addEventListener("click", () => {
      const label = [client.name, client.engagement_name].filter(Boolean).join(" · ");
      const totalCards = cards.length;
      const completed = [...responses.values()].filter(
        (r) => r.state === "answered" || r.state === "skipped",
      ).length;
      const allUploads = [...uploads.values()].flat();
      const fileCount = allUploads.filter((u) => u.kind !== "voice").length;
      const voiceCount = allUploads.filter((u) => u.kind === "voice").length;
      const lines = [`Delete ${label}?`];
      if (totalCards > 0) {
        const parts = [
          `${totalCards} card${totalCards === 1 ? "" : "s"}`,
          `${completed} response${completed === 1 ? "" : "s"}`,
        ];
        if (fileCount > 0) {
          parts.push(`${fileCount} uploaded file${fileCount === 1 ? "" : "s"}`);
        }
        if (voiceCount > 0) {
          parts.push(`${voiceCount} voice note${voiceCount === 1 ? "" : "s"}`);
        }
        lines.push(`This will permanently remove ${parts.join(", ")}.`);
      }
      lines.push("This cannot be undone.");
      openConfirmModal({
        title: "Delete engagement",
        body: lines.join("\n"),
        confirmLabel: "Delete",
        danger: true,
        onConfirm: async () => {
          await adminApi.deleteEngagement(client.id);
          toast("Engagement deleted");
          window.location.hash = "";
        },
      });
    });

    headerEl.querySelector<HTMLButtonElement>("#reset-engagement")?.addEventListener("click", () => {
      const label = [client.name, client.engagement_name].filter(Boolean).join(" · ");
      const completed = [...responses.values()].filter(
        (r) => r.state === "answered" || r.state === "skipped",
      ).length;
      const allUploads = [...uploads.values()].flat();
      const fileCount = allUploads.filter((u) => u.kind !== "voice").length;
      const voiceCount = allUploads.filter((u) => u.kind === "voice").length;
      const lines = [`Reset all answers for ${label}?`];
      const parts: string[] = [];
      if (completed > 0) parts.push(`${completed} response${completed === 1 ? "" : "s"}`);
      if (fileCount > 0) parts.push(`${fileCount} uploaded file${fileCount === 1 ? "" : "s"}`);
      if (voiceCount > 0) parts.push(`${voiceCount} voice note${voiceCount === 1 ? "" : "s"}`);
      if (parts.length > 0) {
        lines.push(`This clears ${parts.join(" and ")}, returning every card to unanswered.`);
      } else {
        lines.push("There are no answers to clear yet.");
      }
      lines.push("The cards and the link stay the same, so the client can start over. This cannot be undone.");
      openConfirmModal({
        title: "Reset answers",
        body: lines.join("\n"),
        confirmLabel: "Reset answers",
        danger: true,
        onConfirm: async () => {
          await adminApi.resetEngagement(client.id);
          toast("Answers reset");
          window.location.reload();
        },
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
        await adminApi.updateEngagement(client.id, { brief: next || null });
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

    wireAttachmentUpload(articleEl, ".edit-attachment");

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

    wireAttachmentUpload(formEl, ".add-attachment");

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

function wireAttachmentUpload(scope: HTMLElement, inputSelector: string): void {
  const input = scope.querySelector<HTMLInputElement>(inputSelector);
  if (!input) return;
  const field = input.closest<HTMLElement>(".attachment-field");
  if (!field) return;
  const trigger = field.querySelector<HTMLButtonElement>(".attachment-upload-trigger");
  const fileInput = field.querySelector<HTMLInputElement>(".attachment-file-input");
  const errorEl = field.querySelector<HTMLElement>(".attachment-error");
  if (!trigger || !fileInput || !errorEl) return;

  const setError = (msg: string | null): void => {
    if (!msg) {
      errorEl.textContent = "";
      errorEl.hidden = true;
      return;
    }
    errorEl.textContent = msg;
    errorEl.hidden = false;
  };

  trigger.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    fileInput.value = ""; // re-pick same file fires change again
    if (!file) return;

    setError(null);
    const originalLabel = trigger.textContent ?? "Upload file";
    trigger.disabled = true;
    trigger.textContent = "Uploading...";
    try {
      const { path } = await adminApi.uploadAttachment(file);
      input.value = path;
      // Pulse the input so the operator notices the path was set
      input.classList.add("attachment-pulse");
      setTimeout(() => input.classList.remove("attachment-pulse"), 800);
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : "Upload failed";
      console.error("attachment upload:", err);
      setError(detail);
    } finally {
      trigger.disabled = false;
      trigger.textContent = originalLabel;
    }
  });
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

        <div class="edit-field attachment-field">
          <span class="edit-label">Active reference (optional)</span>
          <div class="attachment-input-row">
            <input class="input add-attachment" type="text" placeholder="deliverables/example.html or upload below" />
            <button class="btn-secondary-sm attachment-upload-trigger" type="button" data-action="upload-attachment">Upload file</button>
            <input type="file" class="attachment-file-input" hidden accept=".html,.htm,.pdf,.jpg,.jpeg,.png,.gif,.webp,.svg" />
          </div>
          <p class="attachment-help">HTML, PDF, JPEG, PNG, GIF, WEBP, or SVG.</p>
          <div class="attachment-error" hidden></div>
        </div>

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
- [ ] Engagement deleted if access should end
`;

function renderBriefView(client: Engagement): string {
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

function renderBriefEdit(client: Engagement): string {
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

        <div class="edit-field attachment-field">
          <span class="edit-label">Active reference (optional)</span>
          <div class="attachment-input-row">
            <input class="input edit-attachment" type="text" placeholder="deliverables/example.html or upload below" value="${escape(card.attachment_path ?? "")}" />
            <button class="btn-secondary-sm attachment-upload-trigger" type="button" data-action="upload-attachment">Upload file</button>
            <input type="file" class="attachment-file-input" hidden accept=".html,.htm,.pdf,.jpg,.jpeg,.png,.gif,.webp,.svg" />
          </div>
          <p class="attachment-help">HTML, PDF, JPEG, PNG, GIF, WEBP, or SVG.</p>
          <div class="attachment-error" hidden></div>
        </div>

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

// Inline audio players for any voice answers on a card. Rendered on EVERY
// card (voice notes supplement the typed answer), separately from the
// file-upload download links. The org-scoped admin download endpoint
// serves the right mime, so the browser plays whatever the client recorded.
function renderVoicePlayback(uploads: UploadRow[]): string {
  const voice = uploads.filter((u) => u.kind === "voice");
  if (voice.length === 0) return "";
  const players = voice
    .map((u) => {
      const url = adminApi.uploadDownloadUrl(u.id);
      return `<audio class="voice-playback" controls preload="none" src="${escape(url)}"></audio>`;
    })
    .join("");
  return `<div class="voice-answer"><div class="voice-answer-label">Voice answer</div>${players}</div>`;
}

function renderResponseBodyHtml(card: Card, response: ClientResponse | undefined, uploads: UploadRow[]): string {
  const voiceHtml = renderVoicePlayback(uploads);
  if (!response || response.state === "not_started") return `Not yet viewed.${voiceHtml}`;
  if (response.state === "viewed") return `Card opened, no response yet.${voiceHtml}`;

  const v = (response.response_value ?? {}) as ResponseValueShape;
  const noteHtml = v.note ? `<div class="response-note"><strong>Note:</strong> ${escape(v.note)}</div>` : "";

  if (response.state === "skipped") return `Skipped.${noteHtml}${voiceHtml}`;

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
    case "file-upload": {
      const files = uploads.filter((u) => u.kind !== "voice");
      body =
        files.length === 0
          ? "No files uploaded."
          : `<ul class="uploads-list">${files
              .map((u) => {
                const url = adminApi.uploadDownloadUrl(u.id);
                const label = `${escape(u.file_name)} <span class="upload-size">(${formatBytes(u.file_size_bytes)})</span>`;
                return `<li><a href="${escape(url)}" target="_blank" rel="noreferrer noopener" class="upload-link">${label}</a></li>`;
              })
              .join("")}</ul>`;
      break;
    }
    default:
      body = "";
  }
  return body + noteHtml + voiceHtml;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── markdown export plumbing ─────────────────────────────────────────────

function summarizeUploads(uploads: UploadRow[]): UploadInfo[] {
  // Voice answers are not file attachments — keep them out of the Markdown
  // attachment summary so the ClickUp export still lists only `file` uploads.
  return uploads
    .filter((u) => u.kind !== "voice")
    .map((u) => ({ id: u.id, name: u.file_name, sizeBytes: u.file_size_bytes }));
}

function buildSingleCardMarkdown(
  client: Engagement,
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

function downloadFilename(client: Engagement): string {
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

// No HMR accept handler on purpose — changes trigger Vite's default full
// page reload (the removed `import.meta.hot.decline()` used to force this).
main().catch((err) => {
  console.error("Pulse admin failed:", err);
});

void BASE_URL;
