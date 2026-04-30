import type { SupabaseClient } from "@supabase/supabase-js";
import {
  type Card,
  type Client,
  type ClientResponse,
} from "../lib/supabase";
import { getAdminClient } from "../lib/admin-supabase";
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

const PASSWORD_HASH = (import.meta.env.PUBLIC_ADMIN_PASSWORD_HASH ?? "") as string;
const SESSION_KEY = "pulse_admin_session";
const BASE_URL = (import.meta.env.BASE_URL ?? "/") as string;
const PROD_URL = "https://tomdigati.github.io/pulse/";

interface UploadRow {
  id: string;
  card_id: string;
  client_id: string;
  file_name: string;
  file_size_bytes: number;
  storage_path: string;
  mime_type: string | null;
  uploaded_at: string;
}

interface EngagementSummary {
  client: Client;
  cardsTotal: number;
  cardsAnswered: number;
  cardsSkipped: number;
}

const escape = (s: string): string =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

async function sha256(s: string): Promise<string> {
  const buf = new TextEncoder().encode(s);
  const hash = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// ── boot ─────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  const mount = document.getElementById("admin");
  if (!mount) return;

  if (!PASSWORD_HASH) {
    renderConfigError(mount);
    return;
  }

  if (sessionStorage.getItem(SESSION_KEY) === "ok") {
    runAdmin(mount);
    return;
  }

  renderLogin(mount);
}

function renderConfigError(mount: HTMLElement): void {
  mount.innerHTML = `
    <div class="login-card">
      <h1>Admin not configured</h1>
      <p>
        Set <code>PUBLIC_ADMIN_PASSWORD_HASH</code> in <code>.env.local</code>
        and restart the dev server.
      </p>
    </div>
  `;
}

// ── login ────────────────────────────────────────────────────────────────

function renderLogin(mount: HTMLElement, errorMsg?: string): void {
  mount.innerHTML = `
    <form class="login-card" id="login-form">
      <h1>Pulse admin</h1>
      <p>Enter the admin password.</p>
      ${errorMsg ? `<div class="login-error">${escape(errorMsg)}</div>` : ""}
      <input
        id="login-input"
        class="input"
        type="password"
        placeholder="Password"
        autocomplete="current-password"
        autofocus
      />
      <button class="btn btn-primary" type="submit" style="margin-top:8px">
        Sign in
      </button>
    </form>
  `;

  const form = mount.querySelector<HTMLFormElement>("#login-form");
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = mount.querySelector<HTMLInputElement>("#login-input");
    const value = input?.value ?? "";
    if (!value) return;
    const hash = await sha256(value);
    if (hash === PASSWORD_HASH) {
      sessionStorage.setItem(SESSION_KEY, "ok");
      runAdmin(mount);
    } else {
      renderLogin(mount, "Wrong password.");
    }
  });
}

// ── after auth ───────────────────────────────────────────────────────────

interface RouteList {
  kind: "list";
}
interface RouteDetail {
  kind: "detail";
  clientId: string;
}
type Route = RouteList | RouteDetail;

function parseRoute(): Route {
  const hash = window.location.hash.replace(/^#/, "");
  const m = hash.match(/^client\/([0-9a-f-]+)$/i);
  if (m) return { kind: "detail", clientId: m[1] };
  return { kind: "list" };
}

async function runAdmin(mount: HTMLElement): Promise<void> {
  mount.innerHTML = renderShell();
  attachShellHandlers(mount);

  const supabase = getAdminClient();
  const container = mount.querySelector<HTMLElement>(".admin-container")!;

  let route = parseRoute();
  await draw(supabase, container, route);

  window.addEventListener("hashchange", async () => {
    route = parseRoute();
    await draw(supabase, container, route);
  });
}

function renderShell(): string {
  return `
    <div class="admin-page">
      <header class="admin-header">
        <span class="brand">
          <span class="brand-tag">IGTMS</span>· Pulse
          <span class="admin-title" style="margin-left:8px">Admin</span>
        </span>
        <button class="admin-logout" type="button" id="logout">Sign out</button>
      </header>
      <div class="admin-container">
        <div class="loading">Loading...</div>
      </div>
    </div>
  `;
}

function attachShellHandlers(mount: HTMLElement): void {
  mount.querySelector<HTMLButtonElement>("#logout")?.addEventListener("click", () => {
    sessionStorage.removeItem(SESSION_KEY);
    window.location.hash = "";
    renderLogin(mount);
  });
}

async function draw(
  supabase: SupabaseClient,
  container: HTMLElement,
  route: Route
): Promise<void> {
  if (route.kind === "list") {
    container.innerHTML = `<div class="loading">Loading engagements...</div>`;
    const summaries = await loadEngagements(supabase);
    renderList(container, summaries);
    return;
  }

  container.innerHTML = `<div class="loading">Loading responses...</div>`;
  const detail = await loadDetail(supabase, route.clientId);
  if (!detail) {
    container.innerHTML = `<div class="error"><h1 class="error-title">Not found</h1><p class="error-body">No client with that id.</p></div>`;
    return;
  }
  renderDetail(supabase, container, detail);
}

// ── list ─────────────────────────────────────────────────────────────────

async function loadEngagements(
  supabase: SupabaseClient
): Promise<EngagementSummary[]> {
  const { data: clients, error } = await supabase
    .from("clients")
    .select("id, name, org_name, engagement_name, token, created_at, last_active_at")
    .order("created_at", { ascending: false });
  if (error || !clients) {
    console.error("load clients:", error);
    return [];
  }

  const summaries: EngagementSummary[] = [];
  for (const client of clients as Client[]) {
    const [{ count: total }, { count: answered }, { count: skipped }] =
      await Promise.all([
        supabase
          .from("cards")
          .select("id", { count: "exact", head: true })
          .eq("client_id", client.id),
        supabase
          .from("responses")
          .select("id", { count: "exact", head: true })
          .eq("client_id", client.id)
          .eq("state", "answered"),
        supabase
          .from("responses")
          .select("id", { count: "exact", head: true })
          .eq("client_id", client.id)
          .eq("state", "skipped"),
      ]);
    summaries.push({
      client,
      cardsTotal: total ?? 0,
      cardsAnswered: answered ?? 0,
      cardsSkipped: skipped ?? 0,
    });
  }
  return summaries;
}

function renderList(
  container: HTMLElement,
  summaries: EngagementSummary[]
): void {
  if (summaries.length === 0) {
    container.innerHTML = `
      <div class="error"><h1 class="error-title">No engagements yet</h1>
      <p class="error-body">Add a client row to Supabase and refresh.</p></div>
    `;
    return;
  }

  const rows = summaries
    .map((s) => {
      const completed = s.cardsAnswered + s.cardsSkipped;
      return `
      <tr data-client-id="${escape(s.client.id)}">
        <td>
          <div class="client-name">${escape(s.client.name)}</div>
          <div class="org-name">${escape(s.client.org_name ?? "")}</div>
        </td>
        <td>${escape(s.client.engagement_name ?? "")}</td>
        <td>
          <span class="progress-pill">${completed} / ${s.cardsTotal}</span>
        </td>
        <td class="last-active">${escape(formatTimestamp(s.client.last_active_at))}</td>
        <td class="actions">
          <button class="action-link" type="button" data-action="view">View responses</button>
          <button class="action-link" type="button" data-action="copy-link">Copy link</button>
          <button class="action-link danger" type="button" data-action="rotate">Rotate token</button>
        </td>
      </tr>`;
    })
    .join("");

  container.innerHTML = `
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

  container.addEventListener("click", async (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn) return;
    const row = btn.closest<HTMLElement>("tr[data-client-id]");
    if (!row) return;
    const clientId = row.dataset.clientId!;
    const action = btn.dataset.action;

    const summary = summaries.find((s) => s.client.id === clientId);
    if (!summary) return;

    switch (action) {
      case "view":
        window.location.hash = `client/${clientId}`;
        return;
      case "copy-link":
        await navigator.clipboard.writeText(`${PROD_URL}?t=${summary.client.token}`);
        toast("Link copied to clipboard");
        return;
      case "rotate": {
        const ok = window.confirm(
          `Rotate ${summary.client.name}'s token? The current link will stop working immediately.`
        );
        if (!ok) return;
        await rotateToken(summary.client.id);
        return;
      }
    }
  });
}

async function rotateToken(clientId: string): Promise<void> {
  const supabase = getAdminClient();
  // 8 random bytes → 16 hex chars, matching the seed-time token format.
  // 64 bits of entropy is plenty for a private invite link.
  const newToken = Array.from(crypto.getRandomValues(new Uint8Array(8)))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  const { error } = await supabase
    .from("clients")
    .update({ token: newToken })
    .eq("id", clientId);
  if (error) {
    console.error("rotate token:", error);
    toast("Could not rotate token");
    return;
  }
  await navigator.clipboard.writeText(`${PROD_URL}?t=${newToken}`);
  toast("New token copied to clipboard");
  // Refresh the list to pick up the new token.
  const mount = document.getElementById("admin")!;
  const container = mount.querySelector<HTMLElement>(".admin-container")!;
  await draw(supabase, container, { kind: "list" });
}

// ── detail ───────────────────────────────────────────────────────────────

interface DetailData {
  client: Client;
  cards: Card[];
  responses: Map<string, ClientResponse>;
  uploads: Map<string, UploadRow[]>; // keyed by card_id
}

async function loadDetail(
  supabase: SupabaseClient,
  clientId: string
): Promise<DetailData | null> {
  const { data: client, error: clientErr } = await supabase
    .from("clients")
    .select("id, name, org_name, engagement_name, token, created_at, last_active_at")
    .eq("id", clientId)
    .single<Client>();
  if (clientErr || !client) return null;

  const [cardsResult, responsesResult, uploadsResult] = await Promise.all([
    supabase
      .from("cards")
      .select(
        "id, client_id, order_index, category, title, context, question, response_type, options, default_value, skip_allowed, attachment_path, created_at"
      )
      .eq("client_id", clientId)
      .order("order_index", { ascending: true }),
    supabase
      .from("responses")
      .select(
        "id, card_id, client_id, state, response_value, viewed_at, answered_at, created_at, updated_at"
      )
      .eq("client_id", clientId),
    supabase
      .from("uploads")
      .select(
        "id, card_id, client_id, file_name, file_size_bytes, storage_path, mime_type, uploaded_at"
      )
      .eq("client_id", clientId)
      .order("uploaded_at", { ascending: true }),
  ]);

  if (cardsResult.error || !cardsResult.data) return null;

  const responses = new Map<string, ClientResponse>(
    (responsesResult.data ?? []).map((r) => {
      const cr = r as ClientResponse;
      return [cr.card_id, cr];
    })
  );

  const uploads = new Map<string, UploadRow[]>();
  for (const row of (uploadsResult.data ?? []) as UploadRow[]) {
    const list = uploads.get(row.card_id) ?? [];
    list.push(row);
    uploads.set(row.card_id, list);
  }

  return {
    client,
    cards: cardsResult.data as Card[],
    responses,
    uploads,
  };
}

function renderDetail(
  supabase: SupabaseClient,
  container: HTMLElement,
  data: DetailData
): void {
  const { client, cards, responses, uploads } = data;

  // Per-card status overrides keyed by card id.
  const statusOverrides = new Map<string, Status>();

  const cardsHtml = cards
    .map((card) => renderResponseCard(card, responses.get(card.id), uploads.get(card.id) ?? [], statusOverrides))
    .join("");

  container.innerHTML = `
    <button class="back-link" type="button" id="back">← All engagements</button>
    <section class="detail-header">
      <div>
        <h2>${escape(client.name)}</h2>
        <div class="org">${escape(client.org_name ?? "")} · ${escape(client.engagement_name ?? "")}</div>
      </div>
      <div class="detail-actions">
        <button class="btn-secondary-sm" type="button" id="copy-all">Copy all as Markdown</button>
        <button class="btn-secondary-sm" type="button" id="copy-link">Copy link</button>
      </div>
    </section>
    ${cardsHtml}
  `;

  container.querySelector<HTMLButtonElement>("#back")?.addEventListener("click", () => {
    window.location.hash = "";
  });

  container
    .querySelector<HTMLButtonElement>("#copy-link")
    ?.addEventListener("click", async () => {
      await navigator.clipboard.writeText(`${PROD_URL}?t=${client.token}`);
      toast("Link copied to clipboard");
    });

  container
    .querySelector<HTMLButtonElement>("#copy-all")
    ?.addEventListener("click", async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      btn.disabled = true;
      try {
        const md = await buildEngagementMarkdown(supabase, data, statusOverrides);
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

  // Per-card handlers. Re-bound after each card-level re-render via
  // attachCardHandlers so edit/save/cancel keep working after a swap.
  const attachCardHandlers = (articleEl: HTMLElement): void => {
    const cardId = articleEl.dataset.cardId!;
    const card = cards.find((c) => c.id === cardId);
    if (!card) return;

    const select = articleEl.querySelector<HTMLSelectElement>(".status-select");
    select?.addEventListener("change", () => {
      statusOverrides.set(cardId, select.value as Status);
    });

    articleEl
      .querySelector<HTMLButtonElement>("[data-action='copy-card']")
      ?.addEventListener("click", async (e) => {
        const btn = e.currentTarget as HTMLButtonElement;
        btn.disabled = true;
        try {
          const md = await buildSingleCardMarkdown(
            supabase,
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

    articleEl
      .querySelector<HTMLButtonElement>("[data-action='edit-card-start']")
      ?.addEventListener("click", () => {
        swapCardHtml(articleEl, renderEditCardForm(card));
      });

    articleEl
      .querySelector<HTMLButtonElement>("[data-action='edit-card-cancel']")
      ?.addEventListener("click", () => {
        swapCardHtml(
          articleEl,
          renderResponseCard(card, responses.get(card.id), uploads.get(card.id) ?? [], statusOverrides)
        );
      });

    articleEl
      .querySelector<HTMLButtonElement>("[data-action='edit-card-save']")
      ?.addEventListener("click", async (e) => {
        const btn = e.currentTarget as HTMLButtonElement;
        const patch = readEditForm(articleEl, card);
        if (!patch) return;
        btn.disabled = true;
        const original = btn.textContent;
        btn.textContent = "Saving...";
        try {
          const { data: updated, error } = await supabase
            .from("cards")
            .update(patch)
            .eq("id", card.id)
            .select()
            .single<Card>();
          if (error || !updated) {
            console.error("card update:", error);
            toast("Could not save");
            return;
          }
          // Replace in the local cards array so re-renders pick up the
          // new text without a full reload.
          const idx = cards.findIndex((c) => c.id === card.id);
          if (idx >= 0) cards[idx] = updated;
          swapCardHtml(
            articleEl,
            renderResponseCard(updated, responses.get(card.id), uploads.get(card.id) ?? [], statusOverrides)
          );
          toast("Card saved");
        } finally {
          btn.disabled = false;
          btn.textContent = original;
        }
      });
  };

  // swapCardHtml replaces an article's contents and rebinds handlers
  // against the freshly rendered DOM nodes.
  const swapCardHtml = (articleEl: HTMLElement, newHtml: string): void => {
    const tmp = document.createElement("div");
    tmp.innerHTML = newHtml.trim();
    const next = tmp.firstElementChild as HTMLElement | null;
    if (!next) return;
    articleEl.replaceWith(next);
    attachCardHandlers(next);
  };

  for (const articleEl of container.querySelectorAll<HTMLElement>(
    ".response-card[data-card-id]"
  )) {
    attachCardHandlers(articleEl);
  }
}

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
    (s) =>
      `<option value="${escape(s)}"${s === suggested ? " selected" : ""}>${escape(s)}</option>`
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

// readEditForm pulls a partial patch out of the inline edit form. Returns
// null when a required field is empty (the offending input is focused so
// the operator can fix it).
function readEditForm(
  articleEl: HTMLElement,
  card: Card
): Partial<Card> | null {
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

  const isSelect =
    card.response_type === "single-select" || card.response_type === "multi-select";
  let options: string[] | null | undefined;
  if (isSelect && optionsEl) {
    options = optionsEl.value
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
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
  const isSelect =
    card.response_type === "single-select" || card.response_type === "multi-select";
  const optionsText = isSelect && card.options
    ? card.options.join("\n")
    : "";
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
    case "answered":
      return "Answered";
    case "skipped":
      return "Skipped";
    case "viewed":
      return "Viewed";
    case "needs_edit":
      return "Editing";
    case "not_started":
    default:
      return "Not viewed";
  }
}

function stateClassFor(response: ClientResponse | undefined): string {
  if (!response) return "state-pending";
  switch (response.state) {
    case "answered":
      return "state-answered";
    case "skipped":
      return "state-skipped";
    case "viewed":
      return "state-viewed";
    default:
      return "state-pending";
  }
}

function responseBodyMutedClass(response: ClientResponse | undefined): string {
  if (!response) return " muted";
  if (response.state === "viewed" || response.state === "skipped" || response.state === "not_started") {
    return " muted";
  }
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

function renderResponseBodyHtml(
  card: Card,
  response: ClientResponse | undefined,
  uploads: UploadRow[]
): string {
  if (!response || response.state === "not_started") return "Not yet viewed.";
  if (response.state === "viewed") return "Card opened, no response yet.";

  const v = (response.response_value ?? {}) as ResponseValueShape;
  const noteHtml = v.note
    ? `<div class="response-note"><strong>Note:</strong> ${escape(v.note)}</div>`
    : "";

  if (response.state === "skipped") return `Skipped.${noteHtml}`;

  let body: string;
  switch (card.response_type) {
    case "confirm-edit":
      body = v.confirmed
        ? "Confirmed as written."
        : `Edited:\n${v.correction ?? ""}`;
      break;
    case "single-select":
      body = escape(String(v.selected ?? ""));
      break;
    case "multi-select": {
      const arr = Array.isArray(v.selected) ? v.selected : [];
      body =
        arr.length === 0
          ? "None selected."
          : `<ul>${arr.map((s) => `<li>${escape(s)}</li>`).join("")}</ul>`;
      break;
    }
    case "short-text":
    case "long-text":
      body = escape(v.text ?? "");
      break;
    case "document-link":
      body = v.url
        ? `<a href="${escape(v.url)}" target="_blank" rel="noreferrer noopener">${escape(v.url)}</a>`
        : "";
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
          : `<ul>${uploads.map((u) => `<li>${escape(u.file_name)} <span style="color:var(--muted)">(${formatBytes(u.file_size_bytes)})</span></li>`).join("")}</ul>`;
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

async function resolveUploads(
  supabase: SupabaseClient,
  uploads: UploadRow[]
): Promise<UploadInfo[]> {
  const out: UploadInfo[] = [];
  for (const u of uploads) {
    const { data } = await supabase.storage
      .from("pulse-uploads")
      .createSignedUrl(u.storage_path, 60 * 60 * 24 * 7); // 7 days
    out.push({
      id: u.id,
      name: u.file_name,
      signedUrl: data?.signedUrl ?? "",
    });
  }
  return out;
}

async function buildSingleCardMarkdown(
  supabase: SupabaseClient,
  client: Client,
  card: Card,
  response: ClientResponse | undefined,
  uploads: UploadRow[],
  statusOverride: Status | undefined
): Promise<string> {
  const status = statusOverride ?? suggestStatus(card, response);
  const resolved = await resolveUploads(supabase, uploads);
  return renderCardMarkdown({ card, client, response, status, uploads: resolved });
}

async function buildEngagementMarkdown(
  supabase: SupabaseClient,
  data: DetailData,
  overrides: Map<string, Status>
): Promise<string> {
  const blocks: string[] = [];
  for (const card of data.cards) {
    const response = data.responses.get(card.id);
    const status = overrides.get(card.id) ?? suggestStatus(card, response);
    const resolved = await resolveUploads(supabase, data.uploads.get(card.id) ?? []);
    blocks.push(
      renderCardMarkdown({ card, client: data.client, response, status, uploads: resolved })
    );
  }
  return renderEngagementMarkdown(blocks);
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

// Suppress unused-import warning for BASE_URL (kept for potential future routing).
void BASE_URL;
