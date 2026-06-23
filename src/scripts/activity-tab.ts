// Activity tab — the third tab on the Settings page (`#settings/activity`).
//
// Renders the paginated audit-log feed for the active org. Visible to
// any member of the org; the backend is the source of truth on access.
//
// Data flow:
//   • initial load: orgsApi.listActivity({ limit }) → renders rows.
//   • filter change: re-issues listActivity with the new params, replacing
//     the list (the cursor resets).
//   • "Load more": orgsApi.listActivity({ limit, cursor: next_cursor })
//     and appends.
//
// The action enum is mirrored from `audit.py::AUDIT_ACTIONS`. Adding a
// new action requires updating both sides — the test
// `test_audit_log.py::test_action_enum_lock_in` keeps them in sync on
// the backend, and any new action that doesn't appear in
// ACTION_LABELS here renders as the raw enum string (degrades cleanly).
import {
  ApiError,
  type ActivityEntry,
  type ActivityPage,
  type MemberRow,
  orgsApi,
} from "../lib/api";
import { formatTimestamp } from "../lib/format-time";

export interface ActivityTabHostHelpers {
  /** Brief transient toast. */
  toast: (msg: string) => void;
}

export interface RenderActivityTabArgs {
  container: HTMLElement;
  /** Members of the active org — used to populate the "By user"
   * filter dropdown. The list refreshes whenever the user switches
   * tabs back to Activity. */
  members: MemberRow[];
  helpers: ActivityTabHostHelpers;
}

const ACTIONS_PAGE_SIZE = 50;

// Human-readable labels by action enum. Keep in sync with
// `api/pulse_api/audit.py::AUDIT_ACTIONS`.
const ACTION_LABELS: Record<string, string> = {
  "client.create": "Created engagement",
  "client.update": "Edited engagement",
  "client.delete": "Deleted engagement",
  "client.reset": "Reset engagement answers",
  "card.create": "Added card",
  "card.update": "Edited card",
  "card.delete": "Deleted card",
  "card.import": "Imported cards",
  "attachment.upload": "Uploaded attachment",
  "org.update": "Updated organization",
  "org.logo_set": "Updated logo",
  "org.logo_remove": "Removed logo",
  "org.branding": "Updated branding",
  "org.create": "Created organization",
  "org.delete": "Deleted organization",
  "member.invite": "Invited teammate",
  "member.invite_revoke": "Revoked invite",
  "member.role_change": "Changed member role",
  "member.remove": "Removed member",
  "member.join": "Joined organization",
  "api_key.create": "Created API key",
  "api_key.revoke": "Revoked API key",
};

// Ordered list of actions for the filter dropdown — grouped by area.
const FILTER_ACTIONS: ReadonlyArray<string> = [
  "client.create",
  "client.update",
  "client.delete",
  "client.reset",
  "card.create",
  "card.update",
  "card.delete",
  "card.import",
  "attachment.upload",
  "org.update",
  "org.logo_set",
  "org.logo_remove",
  "org.branding",
  "org.create",
  "org.delete",
  "member.invite",
  "member.invite_revoke",
  "member.role_change",
  "member.remove",
  "member.join",
  "api_key.create",
  "api_key.revoke",
];

const escape = (s: string): string =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

interface ActivityState {
  entries: ActivityEntry[];
  nextCursor: string | null;
  actorFilter: string | null;
  actionFilter: string | null;
  loading: boolean;
}

export function renderActivityTab(args: RenderActivityTabArgs): void {
  const { container } = args;
  const state: ActivityState = {
    entries: [],
    nextCursor: null,
    actorFilter: null,
    actionFilter: null,
    loading: true,
  };

  container.innerHTML = renderShell(state, args);
  bindFilters(container, state, args);
  bindLoadMore(container, state, args);

  void loadInitial(container, state, args);
}

function renderShell(
  state: ActivityState,
  args: RenderActivityTabArgs,
): string {
  return `
    <section
      class="settings-section activity-section"
      aria-labelledby="activity-h"
    >
      <h3 class="settings-section-h" id="activity-h">Activity</h3>
      <p class="settings-section-p">
        Every change in this organization shows up here as it happens.
      </p>

      <div class="activity-filterbar" role="search" aria-label="Filter activity">
        <label class="activity-filter">
          <span class="activity-filter-label">By user</span>
          <select id="activity-actor" class="input activity-filter-input">
            <option value="">Anyone</option>
            ${args.members
              .map(
                (m) => `
              <option value="${escape(m.user_id)}">
                ${escape(m.name?.trim() || m.email)}
              </option>`,
              )
              .join("")}
          </select>
        </label>
        <label class="activity-filter">
          <span class="activity-filter-label">By action</span>
          <select id="activity-action" class="input activity-filter-input">
            <option value="">All actions</option>
            ${FILTER_ACTIONS.map(
              (a) =>
                `<option value="${escape(a)}">${escape(ACTION_LABELS[a] ?? a)}</option>`,
            ).join("")}
          </select>
        </label>
        <button
          class="btn-ghost-sm"
          type="button"
          id="activity-reset"
          aria-label="Clear activity filters"
        >Clear filters</button>
      </div>

      <ul
        class="activity-list"
        id="activity-list"
        aria-busy="${state.loading ? "true" : "false"}"
        aria-live="polite"
      ></ul>

      <div class="activity-footer">
        <button
          class="btn-secondary-sm"
          type="button"
          id="activity-load-more"
          hidden
        >Load more</button>
        <span
          class="settings-form-msg"
          id="activity-msg"
          role="status"
          aria-live="polite"
        ></span>
      </div>
    </section>
  `;
}

async function loadInitial(
  container: HTMLElement,
  state: ActivityState,
  args: RenderActivityTabArgs,
): Promise<void> {
  await fetchPage(container, state, args, /* append */ false);
}

async function fetchPage(
  container: HTMLElement,
  state: ActivityState,
  _args: RenderActivityTabArgs,
  append: boolean,
): Promise<void> {
  const list = container.querySelector<HTMLUListElement>("#activity-list");
  const loadBtn = container.querySelector<HTMLButtonElement>(
    "#activity-load-more",
  );
  const msg = container.querySelector<HTMLElement>("#activity-msg");
  if (!list) return;

  list.setAttribute("aria-busy", "true");
  if (msg) {
    msg.textContent = "";
    msg.classList.remove("error", "success");
  }
  if (loadBtn) loadBtn.disabled = true;

  try {
    const page: ActivityPage = await orgsApi.listActivity({
      limit: ACTIONS_PAGE_SIZE,
      cursor: append ? state.nextCursor : null,
      actor_user_id: state.actorFilter,
      action: state.actionFilter,
    });

    if (append) {
      state.entries = [...state.entries, ...page.entries];
    } else {
      state.entries = page.entries;
    }
    state.nextCursor = page.next_cursor;
    state.loading = false;

    list.innerHTML = renderEntries(state.entries);
    if (loadBtn) {
      loadBtn.hidden = state.nextCursor === null;
      loadBtn.disabled = false;
    }
  } catch (err) {
    if (msg) {
      msg.textContent =
        err instanceof ApiError ? err.detail : "Could not load activity.";
      msg.classList.add("error");
    }
    if (loadBtn) loadBtn.disabled = false;
  } finally {
    list.setAttribute("aria-busy", "false");
  }
}

function renderEntries(entries: ActivityEntry[]): string {
  if (entries.length === 0) {
    return `
      <li class="activity-empty">
        <span class="activity-empty-line">No activity yet.</span>
        <p class="muted activity-empty-hint">
          Mutations show up here as they happen.
        </p>
      </li>
    `;
  }
  return entries.map(renderRow).join("");
}

function renderRow(entry: ActivityEntry): string {
  const actorName = entry.actor.name?.trim() || entry.actor.email || "Someone";
  const phrase = formatActivityPhrase(entry, actorName);
  const ts = formatTimestamp(entry.created_at);
  return `
    <li class="activity-row" data-action="${escape(entry.action)}">
      <div class="activity-row-main">
        <span class="activity-row-actor">${escape(actorName)}</span>
        <span class="activity-row-phrase">${phrase}</span>
      </div>
      <span class="activity-row-time" title="${escape(entry.created_at)}">${escape(ts)}</span>
    </li>
  `;
}

/** Produce the second half of an activity row's first line, after the
 * actor name. Returns escaped HTML. Examples:
 *   client.create → "created engagement <em>Renee</em>"
 *   member.role_change → "changed Sara's role from member to owner"
 */
function formatActivityPhrase(
  entry: ActivityEntry,
  _actorName: string,
): string {
  const meta = entry.metadata ?? {};
  switch (entry.action) {
    case "client.create":
      return `created engagement ${emName(meta.name)}`;
    case "client.update":
      return `edited engagement ${emName(meta.name)}`;
    case "client.delete":
      return `deleted engagement ${emName(meta.name)}`;
    case "client.reset": {
      const r = meta.responses_cleared ?? 0;
      const u = meta.uploads_cleared ?? 0;
      return `reset engagement answers (${r} response${r === 1 ? "" : "s"}, ${u} upload${u === 1 ? "" : "s"} cleared)`;
    }
    case "card.create":
      return `added card ${emName(meta.title)}`;
    case "card.update":
      return `edited card ${emName(meta.title)}`;
    case "card.delete":
      return `deleted card ${emName(meta.title)}`;
    case "card.import": {
      const count = typeof meta.count === "number" ? meta.count : 0;
      return `imported ${count} card${count === 1 ? "" : "s"}`;
    }
    case "attachment.upload":
      return `uploaded attachment ${emName(meta.filename)}`;
    case "org.update": {
      const oldName = stringOrNull(meta.old_name);
      const newName = stringOrNull(meta.new_name);
      if (oldName && newName) {
        return `renamed the organization from ${emName(oldName)} to ${emName(newName)}`;
      }
      return `updated the organization`;
    }
    case "org.logo_set":
      return `updated the organization logo`;
    case "org.logo_remove":
      return `removed the organization logo`;
    case "org.create":
      return `created organization ${emName(meta.name)}`;
    case "org.delete":
      return `deleted organization ${emName(meta.name)}`;
    case "member.invite":
      return `invited ${emName(meta.email)} (${escape(stringOrNull(meta.role) ?? "member")})`;
    case "member.invite_revoke":
      return `revoked a pending invite`;
    case "member.role_change":
      return `changed a member's role from ${escape(stringOrNull(meta.from) ?? "—")} to ${escape(stringOrNull(meta.to) ?? "—")}`;
    case "member.remove":
      return `removed a member${meta.former_role ? ` (${escape(stringOrNull(meta.former_role) ?? "")})` : ""}`;
    case "member.join":
      return `joined the organization as ${escape(stringOrNull(meta.role) ?? "member")}`;
    case "api_key.create":
      return `created API key ${emName(meta.label)} (pulse_${escape(stringOrNull(meta.prefix) ?? "…")}…)`;
    case "api_key.revoke":
      return `revoked API key ${emName(meta.label)}`;
    default:
      return escape(ACTION_LABELS[entry.action] ?? entry.action);
  }
}

function emName(value: unknown): string {
  const s = stringOrNull(value);
  return s ? `<em class="activity-target">${escape(s)}</em>` : "<em>(unknown)</em>";
}

function stringOrNull(value: unknown): string | null {
  if (typeof value === "string" && value.length > 0) return value;
  return null;
}

function bindFilters(
  container: HTMLElement,
  state: ActivityState,
  args: RenderActivityTabArgs,
): void {
  const actor = container.querySelector<HTMLSelectElement>("#activity-actor");
  const action = container.querySelector<HTMLSelectElement>("#activity-action");
  const reset = container.querySelector<HTMLButtonElement>("#activity-reset");

  const onChange = (): void => {
    state.actorFilter = actor?.value || null;
    state.actionFilter = action?.value || null;
    state.nextCursor = null;
    void fetchPage(container, state, args, /* append */ false);
  };

  actor?.addEventListener("change", onChange);
  action?.addEventListener("change", onChange);
  reset?.addEventListener("click", () => {
    if (actor) actor.value = "";
    if (action) action.value = "";
    state.actorFilter = null;
    state.actionFilter = null;
    state.nextCursor = null;
    void fetchPage(container, state, args, /* append */ false);
  });
}

function bindLoadMore(
  container: HTMLElement,
  state: ActivityState,
  args: RenderActivityTabArgs,
): void {
  const btn = container.querySelector<HTMLButtonElement>(
    "#activity-load-more",
  );
  btn?.addEventListener("click", () => {
    if (state.nextCursor === null) return;
    void fetchPage(container, state, args, /* append */ true);
  });
}
