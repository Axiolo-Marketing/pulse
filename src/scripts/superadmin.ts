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
  type ReactiveUsageEngagementRow,
  type ReactiveUsageMonthlyRow,
  type ReactiveUsageOrgRow,
  type ReactiveUsageResponse,
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

// Reactive-cards usage report window choices (days).
const USAGE_DAY_OPTIONS = [30, 90] as const;
const DEFAULT_USAGE_DAYS = 30;

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
  bindUsageWindowButtons(container);

  await Promise.all([
    refreshList(container, helpers),
    refreshUsage(container, DEFAULT_USAGE_DAYS),
  ]);
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

      <section class="settings-section" aria-labelledby="superadmin-usage-h">
        <h3 class="settings-section-h" id="superadmin-usage-h">Reactive cards usage</h3>
        <p class="settings-section-p">
          LLM calls, tokens, and estimated cost per org — monitoring only, not a billing surface.
        </p>
        <div class="superadmin-usage-controls" role="group" aria-label="Usage window">
          ${USAGE_DAY_OPTIONS.map(
            (d) => `
            <button class="btn-secondary-sm" type="button" data-usage-days="${d}"
                    aria-pressed="${d === DEFAULT_USAGE_DAYS ? "true" : "false"}">${d} days</button>
          `,
          ).join("")}
        </div>
        <div id="superadmin-usage-slot" class="superadmin-list-slot"></div>

        <h4 class="settings-section-h superadmin-usage-subhead" id="superadmin-usage-engagement-h">
          By engagement
        </h4>
        <p class="settings-section-p">
          Same window as above, broken down per engagement — only engagements with at least one generation appear.
        </p>
        <div id="superadmin-usage-engagement-slot" class="superadmin-list-slot"></div>
      </section>

      <section class="settings-section" aria-labelledby="superadmin-usage-monthly-h">
        <h3 class="settings-section-h" id="superadmin-usage-monthly-h">Monthly cost by org</h3>
        <p class="settings-section-p">
          Trailing 6 calendar months, most recent first — independent of the window above.
        </p>
        <div id="superadmin-usage-monthly-slot" class="superadmin-list-slot"></div>
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
            <th scope="col">Reactive cards</th>
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
      <td class="superadmin-reactive-cell">
        <label class="superadmin-reactive-toggle-label">
          <input type="checkbox" class="superadmin-reactive-toggle" data-action="toggle-reactive"
                 aria-label="Reactive cards for ${escape(row.name)}"
                 ${row.reactive_cards_allowed ? "checked" : ""} />
        </label>
      </td>
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
      <label class="superadmin-reactive-toggle-label superadmin-card-reactive">
        <input type="checkbox" class="superadmin-reactive-toggle" data-action="toggle-reactive"
               aria-label="Reactive cards for ${escape(row.name)}"
               ${row.reactive_cards_allowed ? "checked" : ""} />
        Reactive cards
      </label>
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

// ── Reactive-cards usage report ─────────────────────────────────────────────

function formatTokenCount(n: number): string {
  return n.toLocaleString();
}

function formatCostUsd(n: number): string {
  return `$${n.toFixed(4)}`;
}

async function refreshUsage(
  container: HTMLElement,
  days: number,
): Promise<void> {
  const slot = container.querySelector<HTMLElement>("#superadmin-usage-slot");
  const engagementSlot = container.querySelector<HTMLElement>(
    "#superadmin-usage-engagement-slot",
  );
  const monthlySlot = container.querySelector<HTMLElement>(
    "#superadmin-usage-monthly-slot",
  );
  if (!slot) return;
  slot.innerHTML = `<p class="superadmin-loading">Loading usage…</p>`;
  if (engagementSlot) {
    engagementSlot.innerHTML = `<p class="superadmin-loading">Loading usage…</p>`;
  }
  if (monthlySlot) {
    monthlySlot.innerHTML = `<p class="superadmin-loading">Loading usage…</p>`;
  }
  try {
    // One fetch backs all three tables: `orgs`/`totals` and `engagements`
    // share this `days` window, while `monthly` is always the trailing 6
    // calendar months regardless of `days` — so it doesn't need its own
    // fetch or its own window control.
    const report = await superadminApi.reactiveUsage({ days });
    slot.innerHTML = renderUsageTable(report);
    if (engagementSlot) {
      engagementSlot.innerHTML = renderEngagementUsageTable(report);
    }
    if (monthlySlot) {
      monthlySlot.innerHTML = renderMonthlyUsageTable(report);
    }
  } catch (err) {
    const detail = err instanceof ApiError ? err.detail : "Could not load";
    slot.innerHTML = `<p class="superadmin-error">${escape(detail)}</p>`;
    if (engagementSlot) {
      engagementSlot.innerHTML = `<p class="superadmin-error">${escape(detail)}</p>`;
    }
    if (monthlySlot) {
      monthlySlot.innerHTML = `<p class="superadmin-error">${escape(detail)}</p>`;
    }
  }
}

function usageRowMarkup(row: ReactiveUsageOrgRow): string {
  return `
    <tr>
      <th scope="row">${escape(row.org_name)}</th>
      <td class="num">${row.generations}</td>
      <td class="num">${row.completed}</td>
      <td class="num">${row.skipped}</td>
      <td class="num">${row.failed}</td>
      <td class="num">${formatTokenCount(row.input_tokens)} / ${formatTokenCount(row.output_tokens)}</td>
      <td class="num">${formatCostUsd(row.cost_usd)}</td>
    </tr>
  `;
}

function renderUsageTable(report: ReactiveUsageResponse): string {
  if (report.orgs.length === 0) {
    return `
      <p class="superadmin-empty-list">
        No reactive-cards activity in the last ${report.days} days.
      </p>
    `;
  }
  const totalsRow = `
    <tr class="superadmin-usage-totals">
      <th scope="row">All organizations</th>
      <td class="num">${report.totals.generations}</td>
      <td class="num">${report.totals.completed}</td>
      <td class="num">${report.totals.skipped}</td>
      <td class="num">${report.totals.failed}</td>
      <td class="num">${formatTokenCount(report.totals.input_tokens)} / ${formatTokenCount(report.totals.output_tokens)}</td>
      <td class="num">${formatCostUsd(report.totals.cost_usd)}</td>
    </tr>
  `;
  return `
    <div class="superadmin-usage-table-wrap" role="region" aria-label="Reactive cards usage">
      <table class="superadmin-table">
        <thead>
          <tr>
            <th scope="col">Organization</th>
            <th scope="col" class="num">Calls</th>
            <th scope="col" class="num">Completed</th>
            <th scope="col" class="num">Skipped</th>
            <th scope="col" class="num">Failed</th>
            <th scope="col" class="num">Tokens (in / out)</th>
            <th scope="col" class="num">Est. cost</th>
          </tr>
        </thead>
        <tbody>
          ${report.orgs.map(usageRowMarkup).join("")}
          ${totalsRow}
        </tbody>
      </table>
    </div>
  `;
}

function engagementUsageRowMarkup(row: ReactiveUsageEngagementRow): string {
  return `
    <tr>
      <th scope="row">
        ${escape(row.engagement_label)}
        <span class="superadmin-usage-org-name">${escape(row.org_name)}</span>
      </th>
      <td class="num">${row.generations}</td>
      <td class="num">${formatTokenCount(row.input_tokens)} / ${formatTokenCount(row.output_tokens)}</td>
      <td class="num">${formatCostUsd(row.cost_usd)}</td>
    </tr>
  `;
}

/** Per-engagement usage/cost drill-down for the same `days` window as
 * `renderUsageTable`. Only engagements with at least one generation in
 * the window are present in `report.engagements` (the backend already
 * filters via an inner join), so an empty array just means no activity. */
function renderEngagementUsageTable(report: ReactiveUsageResponse): string {
  if (report.engagements.length === 0) {
    return `
      <p class="superadmin-empty-list">
        No engagements with reactive-cards activity in the last ${report.days} days.
      </p>
    `;
  }
  return `
    <div class="superadmin-usage-table-wrap" role="region" aria-label="Reactive cards usage by engagement">
      <table class="superadmin-table">
        <thead>
          <tr>
            <th scope="col">Engagement</th>
            <th scope="col" class="num">Calls</th>
            <th scope="col" class="num">Tokens (in / out)</th>
            <th scope="col" class="num">Est. cost</th>
          </tr>
        </thead>
        <tbody>
          ${report.engagements.map(engagementUsageRowMarkup).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function monthlyUsageRowMarkup(row: ReactiveUsageMonthlyRow): string {
  return `
    <tr>
      <th scope="row">${escape(row.month)}</th>
      <td>${escape(row.org_name)}</td>
      <td class="num">${row.generations}</td>
      <td class="num">${formatTokenCount(row.input_tokens)} / ${formatTokenCount(row.output_tokens)}</td>
      <td class="num">${formatCostUsd(row.cost_usd)}</td>
    </tr>
  `;
}

/** Monthly per-org cost trend, trailing 6 calendar months, most recent
 * first — always present in `report.monthly` regardless of the `days`
 * window selector, so this renders from the same fetch. */
function renderMonthlyUsageTable(report: ReactiveUsageResponse): string {
  if (report.monthly.length === 0) {
    return `
      <p class="superadmin-empty-list">
        No reactive-cards activity in the last 6 months.
      </p>
    `;
  }
  return `
    <div class="superadmin-usage-table-wrap" role="region" aria-label="Monthly reactive cards cost by org">
      <table class="superadmin-table">
        <thead>
          <tr>
            <th scope="col">Month</th>
            <th scope="col">Organization</th>
            <th scope="col" class="num">Calls</th>
            <th scope="col" class="num">Tokens (in / out)</th>
            <th scope="col" class="num">Est. cost</th>
          </tr>
        </thead>
        <tbody>
          ${report.monthly.map(monthlyUsageRowMarkup).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function bindUsageWindowButtons(container: HTMLElement): void {
  const buttons = container.querySelectorAll<HTMLButtonElement>(
    "[data-usage-days]",
  );
  for (const btn of buttons) {
    btn.addEventListener("click", async () => {
      const days = Number(btn.dataset.usageDays);
      if (!Number.isFinite(days) || days <= 0) return;
      for (const b of buttons) {
        b.setAttribute("aria-pressed", b === btn ? "true" : "false");
      }
      await refreshUsage(container, days);
    });
  }
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
  // paintList() replaces listEl's innerHTML but listEl itself persists,
  // so this runs again after every refreshList(). Bind the delegated
  // listeners exactly once or each repaint stacks another copy (double
  // PATCHes + double toasts on the reactive toggle). The handlers read
  // everything from the DOM at event time, so binding once is safe.
  if (listEl.dataset.actionsBound === "true") {
    void rows;
    return;
  }
  listEl.dataset.actionsBound = "true";

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

  // Reactive-cards allow toggle — "change" (not "click") so the fired
  // event always carries the checkbox's already-committed new value.
  // Reverts the checkbox on failure rather than doing a full list
  // repaint, so an operator flipping several orgs in a row doesn't lose
  // scroll position on every toggle.
  listEl.addEventListener("change", (e) => {
    const target = e.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.dataset.action !== "toggle-reactive") return;

    const rowEl = target.closest<HTMLElement>(
      ".superadmin-row, .superadmin-card",
    );
    const orgId = rowEl?.dataset.orgId;
    const orgName = rowEl?.dataset.orgName ?? "this organization";
    if (!orgId) return;

    const allowed = target.checked;
    target.disabled = true;
    superadminApi
      .updateOrgFlags(orgId, { reactive_cards_allowed: allowed })
      .then(() => {
        helpers.toast(
          `Reactive cards ${allowed ? "enabled" : "disabled"} for ${orgName}`,
        );
      })
      .catch((err: unknown) => {
        target.checked = !allowed;
        const detail =
          err instanceof ApiError ? err.detail : "Could not update";
        helpers.toast(detail);
      })
      .finally(() => {
        target.disabled = false;
      });
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
