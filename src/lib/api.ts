// Typed fetch wrapper for the Pulse API. Replaces the supabase-js client.
//
// Two auth modes:
//   - Client-facing: 16-hex token in URL → `X-Pulse-Token` header on
//     each request. Methods on `clientApi` take a `token` argument.
//   - Admin: signed-cookie session set by the backend after login.
//     `credentials: 'include'` ships the cookie automatically. Methods
//     on `adminApi` (next phase) just need to be authed.
//
// The base URL is `PUBLIC_API_BASE_URL` (inlined at build time). In
// production behind the same nginx as the frontend, this can be empty
// and fetches become same-origin relative URLs.

export const API_BASE = ((import.meta.env.PUBLIC_API_BASE_URL ?? "") as string)
  .replace(/\/$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`HTTP ${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

interface RequestOpts {
  method?: string;
  body?: BodyInit | null;
  token?: string;
  headers?: HeadersInit;
}

async function request<T>(path: string, opts: RequestOpts = {}): Promise<T> {
  const headers = new Headers(opts.headers);
  if (opts.token) headers.set("X-Pulse-Token", opts.token);
  if (
    opts.body !== undefined &&
    opts.body !== null &&
    !(opts.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method: opts.method ?? "GET",
    body: opts.body ?? null,
    headers,
    credentials: "include",
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // body wasn't JSON; keep statusText
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  // GET /api/files/{id} returns a streamed binary — callers use the
  // raw fetch, not this wrapper. All other endpoints return JSON.
  return (await res.json()) as T;
}

// ── Domain types (canonical home — other modules import from here) ────────

/** Per-org branding/theme. Every field is optional; a missing/null value
 * means "use the product default" (see `BRANDING_DEFAULTS` in
 * `branding.ts`). The `font` slug must be one of the `FONT_OPTIONS` slugs,
 * which equal the backend's `ALLOWED_FONTS`. The shape is the cross-team
 * contract — the backend returns and accepts the identical object. */
export interface BrandingSettings {
  /** Primary brand colour, `"#RRGGBB"`. Drives `--primary` plus two
   * derived shades (`--primary-dark`, `--primary-soft`). */
  brand_color?: string | null;
  /** Page background, `"#RRGGBB"` → `--surface-muted`. */
  background_color?: string | null;
  /** Body text colour, `"#RRGGBB"` → `--ink`. */
  text_color?: string | null;
  /** A `FONT_OPTIONS` slug → `--font-sans`. */
  font?: string | null;
}

export type ResponseType =
  | "confirm-edit"
  | "single-select"
  | "multi-select"
  | "short-text"
  | "long-text"
  | "file-upload"
  | "document-link"
  | "contact-share";

export type ResponseState =
  | "not_started"
  | "viewed"
  | "answered"
  | "skipped"
  | "needs_edit";

export interface Engagement {
  id: string;
  /** The owning client's (company) name. On `/api/me` + admin detail
   * views this is joined in server-side from `clients.name`. */
  name: string;
  /** The owning client id (admin views; absent on `/api/me`). */
  client_id?: string;
  engagement_name: string | null;
  brief: string | null;
  created_at: string;
  /** Last activity timestamp. Post multi-respondent migration the magic
   * link lives on the recipient, not the engagement — `/api/me` returns
   * the active recipient's `last_active_at`; the admin detail engagement
   * object no longer carries it (recipients do). Optional/nullable here so
   * both shapes type-check. */
  last_active_at?: string | null;
  /** Whether voice recording is enabled for this engagement. Defaults
   * `false` — voice is opt-in per engagement. The client deck shows the
   * record control only when this is `true`; on `/api/me` the field is
   * always present. Admin views also carry it (operators toggle it from
   * the engagement edit form). */
  voice_enabled: boolean;
  /** Whether reminder emails are enabled for this engagement's recipients
   * (default `false`). Present on admin views; operators toggle it from the
   * engagement edit form. */
  reminders_enabled?: boolean;
  /** The greeting name for the active recipient on `/api/me` (the deck
   * bootstrap), or `null` when the recipient has no name. Admin views
   * don't carry it — recipients are listed separately there. */
  recipient_name?: string | null;
  /** Optional brand logo path for the operator's organization. When
   * `/api/me` returns this, the client-facing deck renders it in the
   * top-bar instead of the default Axiolo wordmark. Backend wires this
   * through in a follow-up — frontend already handles the field
   * gracefully when absent (treats it as `null`). */
  org_logo_path?: string | null;
  /** The operator org's branding/theme. When `/api/me` returns this, the
   * client deck applies it via `applyBranding` on boot. Absent/null →
   * the deck keeps the product defaults. */
  org_branding?: BrandingSettings | null;
}

export interface Card {
  id: string;
  engagement_id: string;
  order_index: number;
  category: string;
  title: string;
  context: string;
  question: string;
  response_type: ResponseType;
  options: string[] | null;
  default_value: string | null;
  skip_allowed: boolean;
  attachment_path: string | null;
  created_at: string;
}

export interface ClientResponse {
  id: string;
  card_id: string;
  engagement_id: string;
  /** The recipient this answer belongs to. Post multi-respondent migration
   * each recipient answers the shared cards independently, so responses are
   * keyed by `(recipient_id, card_id)`. */
  recipient_id: string;
  state: ResponseState;
  response_value: unknown;
  viewed_at: string | null;
  answered_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface UploadRow {
  id: string;
  card_id: string;
  engagement_id: string;
  /** The recipient this upload belongs to (multi-respondent migration —
   * uploads are keyed by `(recipient_id, card_id)` in the admin viewer). */
  recipient_id: string;
  file_name: string;
  file_size_bytes: number;
  storage_path: string;
  mime_type: string | null;
  /** Upload discriminator: `"file"` for answer attachments (the
   * `file-upload` card type), `"voice"` for a recorded voice answer. The
   * operator viewer renders voice uploads as an inline `<audio>` player. */
  kind: "file" | "voice";
  uploaded_at: string;
}

// ── Client-facing API ─────────────────────────────────────────────────────

export const clientApi = {
  me: (token: string): Promise<Engagement> => request("/api/me", { token }),

  cards: (token: string): Promise<Card[]> => request("/api/cards", { token }),

  responses: (token: string): Promise<ClientResponse[]> =>
    request("/api/responses", { token }),

  uploads: (token: string): Promise<UploadRow[]> =>
    request("/api/uploads", { token }),

  markViewed: (token: string, cardId: string): Promise<ClientResponse> =>
    request("/api/responses/view", {
      method: "POST",
      token,
      body: JSON.stringify({ card_id: cardId }),
    }),

  saveResponse: (
    token: string,
    args: { card_id: string; state: ResponseState; response_value: unknown }
  ): Promise<ClientResponse> =>
    request("/api/responses", {
      method: "POST",
      token,
      body: JSON.stringify(args),
    }),

  heartbeat: (token: string): Promise<{ status: string }> =>
    request("/api/me/heartbeat", { method: "PATCH", token }),

  /** Upload a file (or recorded blob) against a card. `kind` defaults to
   * `"file"`; voice answers pass `{ kind: "voice", filename: "voice.webm" }`.
   * A blob has no `.name`, so the optional `filename` names the part. */
  upload: (
    token: string,
    cardId: string,
    file: Blob,
    opts: { kind?: "file" | "voice"; filename?: string } = {},
  ): Promise<UploadRow> => {
    const fd = new FormData();
    fd.append("card_id", cardId);
    const name =
      opts.filename ?? (file instanceof File ? file.name : "file");
    fd.append("file", file, name);
    fd.append("kind", opts.kind ?? "file");
    return request("/api/uploads", { method: "POST", token, body: fd });
  },

  deleteUpload: (token: string, uploadId: string): Promise<void> =>
    request(`/api/uploads/${uploadId}`, { method: "DELETE", token }),

  /** Fetch an upload's bytes (token in the `X-Pulse-Token` header, never
   * the URL) and return an object URL for inline playback/preview — used
   * for voice answers, whose `<audio src>` can't carry the auth header.
   * Returns `null` on any failure so the caller can fall back gracefully.
   * The caller owns the returned object URL and must revoke it. */
  fileObjectUrl: async (token: string, uploadId: string): Promise<string | null> => {
    try {
      const res = await fetch(`${API_BASE}/api/files/${uploadId}`, {
        headers: { "X-Pulse-Token": token },
        credentials: "include",
      });
      if (!res.ok) return null;
      const blob = await res.blob();
      return URL.createObjectURL(blob);
    } catch {
      return null;
    }
  },

  /** Fetch the operator org's logo as a Blob and return an object URL the
   * client deck can use as an `<img src>`. The token rides in the
   * `X-Pulse-Token` header (never the URL — the logo bytes are not a
   * public asset). Returns `null` on 404 (no logo) or any error so the
   * caller can fall back to the Axiolo wordmark without branching on
   * status codes. Caller owns the returned object URL — create it once
   * and reuse it; revoke when the deck tears down. */
  logoObjectUrl: async (token: string): Promise<string | null> => {
    try {
      const res = await fetch(`${API_BASE}/api/me/logo`, {
        headers: { "X-Pulse-Token": token },
        credentials: "include",
      });
      if (!res.ok) return null;
      const blob = await res.blob();
      return URL.createObjectURL(blob);
    } catch {
      return null;
    }
  },
};

// ── URL helper: the streamed download endpoint goes through a regular
// <a href> or <iframe src>, but the X-Pulse-Token has to be on a header.
// For now, callers can fetch() it themselves with the token; embedded
// previews aren't supported.
export function fileUrl(uploadId: string): string {
  return `${API_BASE}/api/files/${uploadId}`;
}

// ── Admin auth + admin API (session cookie via credentials: include) ──────

/** Shape of `/api/auth/me`. Post multi-tenant migration the operator's
 * admin-ness is implicit in their org membership (a user with no active
 * org cannot reach any `/api/admin/*` route). `is_superadmin` is the
 * separate cross-org tier (PR 5). */
export interface AuthUser {
  id: string;
  email: string;
  name: string | null;
  is_superadmin: boolean;
  active_org_id: string | null;
  email_verified_at: string | null;
  has_password: boolean;
}

export interface OAuthIdentitySummary {
  provider: string;
  linked_at: string;
}

export interface ApiKeySummary {
  id: string;
  label: string;
  prefix: string;
  /** Org the key is scoped to. Bearer auth always flips the request
   * into a `pulse_member` session against this org — never the cookie's
   * `active_org_id`. */
  org_id: string;
  last_used_at: string | null;
  created_at: string;
}

export interface ApiKeyWithSecret extends ApiKeySummary {
  /** Full `pulse_<32-hex>` raw key. ONLY returned by POST /api/auth/me/api-keys
   * once at creation; never persisted, never re-fetchable from the list. */
  key: string;
}

// ── Org / membership / invite types (PR 4) ────────────────────────────────

/** Slim org row returned by `/api/me/orgs` and `/api/me/switch-org`. */
export interface OrgSummary {
  id: string;
  name: string;
  slug: string;
  /** The caller's role in this org: `"owner"` or `"member"`. */
  role: string;
  logo_path: string | null;
}

/** Full org payload from `/api/orgs/me`. Drives the Organization tab header. */
export interface OrgDetails {
  id: string;
  name: string;
  slug: string;
  logo_path: string | null;
  /** The caller's role in this org: `"owner"` or `"member"`. */
  role: string;
  member_count: number;
  pending_invite_count: number;
  /** The org's branding/theme, or `null` when nothing has been set (all
   * defaults). Drives the Brand & theme settings form and the live
   * admin-console theme via `applyBranding`. */
  branding: BrandingSettings | null;
}

export interface MemberRow {
  user_id: string;
  email: string;
  name: string | null;
  /** `"owner"` or `"member"`. */
  role: string;
  joined_at: string;
}

export interface InviteSummary {
  id: string;
  email: string;
  role: string;
  created_at: string;
  expires_at: string;
  invited_by_email: string | null;
}

/** Actor sub-payload of an activity entry. Each field can be `null` when
 * the row was emitted without a user (system maintenance) or when the
 * user was removed after the action — the row stays via
 * `ON DELETE SET NULL` on `audit_logs.user_id`. */
export interface ActivityActor {
  user_id: string | null;
  email: string | null;
  name: string | null;
}

/** One row of the activity feed. `action` is a stable enum string;
 * the UI maps it to a human-readable label via `formatActivityRow`
 * in `activity-tab.ts`. */
export interface ActivityEntry {
  id: string;
  created_at: string;
  actor: ActivityActor;
  action: string;
  target_type: string | null;
  target_id: string | null;
  metadata: Record<string, unknown> | null;
}

/** Paginated payload returned by `GET /api/orgs/me/activity`. */
export interface ActivityPage {
  entries: ActivityEntry[];
  /** Opaque cursor — pass it back as `cursor` to fetch the next page.
   * `null` when the current page is the last one. */
  next_cursor: string | null;
}

export interface ListActivityArgs {
  limit?: number;
  cursor?: string | null;
  actor_user_id?: string | null;
  action?: string | null;
}

/** Public invite-acceptance metadata from `GET /api/invites/{token}`. */
export interface InviteMetadata {
  org_name: string;
  email: string;
  role: string;
  expires_at: string;
  /** Resolved status; the acceptance UI branches on this. */
  status: "pending" | "expired" | "accepted" | "revoked";
}

export interface PasswordAcceptResponse {
  user_id: string;
  org_id: string;
  role: string;
}

export interface OAuthAcceptResponse {
  redirect_url: string;
}

export interface EngagementSummary {
  id: string;
  /** Owning client (company) id — the admin list buckets engagements by
   * this. */
  client_id: string;
  /** Owning client name (joined server-side). */
  client_name: string;
  /** Engagement owner's display name, or `null` when the owner couldn't
   * be attributed (e.g. backfilled engagements with no create audit, or
   * a removed user). */
  owner_name: string | null;
  /** Engagement owner's email, or `null`. */
  owner_email: string | null;
  engagement_name: string | null;
  brief: string | null;
  created_at: string;
  last_active_at: string | null;
  /** Whether voice recording is enabled for this engagement (default
   * `false`). Operators flip it from the engagement edit form. */
  voice_enabled: boolean;
  /** Whether reminder emails are enabled for this engagement's recipients
   * (default `false`). Operators flip it from the engagement edit form. */
  reminders_enabled: boolean;
  total_cards: number;
  /** Number of recipients (own magic links) on this engagement. The list
   * progress pill reads `completed_recipients / recipients_count`. */
  recipients_count: number;
  /** How many recipients have completed every card. Drives the derived
   * status: complete when it equals `recipients_count` (and that's > 0). */
  completed_recipients: number;
  /** Total answered+skipped responses across *all* recipients. The list
   * badge shows this over the expected total (`total_cards *
   * recipients_count`) as deck-wide answer progress. */
  answered_responses: number;
}

/** One recipient of an engagement — its own magic link (`token`), answers
 * the shared cards independently, and is attributable. Returned by the
 * recipients list, the detail payload's `recipients`, and add-recipient. */
export interface Recipient {
  id: string;
  engagement_id: string;
  email: string | null;
  name: string | null;
  /** The recipient's 16-hex magic-link token. Copy-link builds the
   * `?t=<token>` URL from this — there's no engagement-level token. */
  token: string;
  last_active_at: string | null;
  invited_at: string | null;
  last_reminded_at: string | null;
  reminder_count: number;
  unsubscribed_at: string | null;
  created_at: string;
  /** How many of the shared cards this recipient has answered/skipped. */
  completed_count: number;
  /** Total shared cards on the engagement (same for every recipient). */
  total_cards: number;
}

/** One real client (company), operator-only. Powers the admin list's
 * client grouping and the new-engagement autocomplete. */
export interface ClientSummary {
  id: string;
  name: string;
  created_at: string;
}

export type Client = ClientSummary;

export interface EngagementDetail {
  engagement: Engagement;
  recipients: Recipient[];
  cards: Card[];
  responses: ClientResponse[];
  uploads: UploadRow[];
}

export interface CreateEngagementArgs {
  /** Existing client to attach the engagement to (from the autocomplete).
   * Provide this OR `client_name`. */
  client_id?: string;
  /** Free-form client name; the backend get-or-creates a client by name
   * in the active org. Provide this OR `client_id`. */
  client_name?: string;
  engagement_name?: string | null;
}

export interface UpdateEngagementArgs {
  engagement_name?: string | null;
  brief?: string | null;
  /** Toggle the per-engagement voice recorder. Omit to leave it
   * unchanged — the backend keys off `model_dump(exclude_unset=True)`,
   * so an absent key never writes the column. */
  voice_enabled?: boolean;
  /** Toggle reminder emails for this engagement's recipients. Omit to
   * leave it unchanged (same `exclude_unset` semantics as `voice_enabled`). */
  reminders_enabled?: boolean;
}

export interface CreateCardArgs {
  category: string;
  title: string;
  context: string;
  question: string;
  response_type: ResponseType;
  options?: string[] | null;
  default_value?: string | null;
  skip_allowed?: boolean;
  attachment_path?: string | null;
}

export type UpdateCardArgs = Partial<Omit<CreateCardArgs, "response_type">>;

export const authApi = {
  me: (): Promise<AuthUser> => request("/api/auth/me"),

  signup: (args: { email: string; password: string; name?: string }): Promise<AuthUser> =>
    request("/api/auth/signup", { method: "POST", body: JSON.stringify(args) }),

  login: (email: string, password: string): Promise<AuthUser> =>
    request("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  logout: (): Promise<{ status: string }> =>
    request("/api/auth/logout", { method: "POST" }),

  verifyEmail: (token: string): Promise<AuthUser> =>
    request("/api/auth/verify-email", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),

  forgotPassword: (email: string): Promise<{ status: string }> =>
    request("/api/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  resetPassword: (token: string, new_password: string): Promise<{ status: string }> =>
    request("/api/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token, new_password }),
    }),

  updateProfile: (args: { name: string | null }): Promise<AuthUser> =>
    request("/api/auth/me", {
      method: "PATCH",
      body: JSON.stringify(args),
    }),

  changePassword: (args: {
    current_password?: string | null;
    new_password: string;
  }): Promise<AuthUser> =>
    request("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify(args),
    }),

  listIdentities: (): Promise<OAuthIdentitySummary[]> =>
    request("/api/auth/me/identities"),

  listApiKeys: (): Promise<ApiKeySummary[]> =>
    request("/api/auth/me/api-keys"),

  createApiKey: (args: {
    label: string;
    org_id?: string | null;
  }): Promise<ApiKeyWithSecret> =>
    request("/api/auth/me/api-keys", {
      method: "POST",
      body: JSON.stringify(args),
    }),

  revokeApiKey: (id: string): Promise<void> =>
    request(`/api/auth/me/api-keys/${id}`, { method: "DELETE" }),

  // Frontend navigates the browser to this URL. The backend redirects to
  // Google/Microsoft with the right state cookie set.
  oauthAuthorizeUrl: (provider: "google" | "microsoft"): string =>
    `${API_BASE}/api/auth/${provider}/authorize`,
};

export const adminApi = {
  listEngagements: (): Promise<EngagementSummary[]> =>
    request("/api/admin/engagements"),

  getEngagement: (id: string): Promise<EngagementDetail> =>
    request(`/api/admin/engagements/${id}`),

  createEngagement: (args: CreateEngagementArgs): Promise<Engagement> =>
    request("/api/admin/engagements", {
      method: "POST",
      body: JSON.stringify(args),
    }),

  updateEngagement: (id: string, args: UpdateEngagementArgs): Promise<Engagement> =>
    request(`/api/admin/engagements/${id}`, {
      method: "PATCH",
      body: JSON.stringify(args),
    }),

  deleteEngagement: (id: string): Promise<void> =>
    request(`/api/admin/engagements/${id}`, { method: "DELETE" }),

  /** Clear all answers + uploaded files for a fresh restart. Cards and the
   * link are preserved. Returns counts of what was cleared. */
  resetEngagement: (id: string): Promise<{ responses_cleared: number; uploads_cleared: number }> =>
    request(`/api/admin/engagements/${id}/reset`, { method: "POST" }),

  // ── Recipients (per-respondent magic links) ──────────────────────────
  listRecipients: (engagementId: string): Promise<Recipient[]> =>
    request(`/api/admin/engagements/${engagementId}/recipients`),

  /** Add a recipient to an engagement. Throws `ApiError` with status 409 if
   * the email is already a recipient (caller toasts "already a recipient");
   * 404 if the engagement isn't found. */
  addRecipient: (
    engagementId: string,
    args: { email: string; name?: string },
  ): Promise<Recipient> =>
    request(`/api/admin/engagements/${engagementId}/recipients`, {
      method: "POST",
      body: JSON.stringify(args),
    }),

  removeRecipient: (engagementId: string, recipientId: string): Promise<void> =>
    request(`/api/admin/engagements/${engagementId}/recipients/${recipientId}`, {
      method: "DELETE",
    }),

  createCard: (engagementId: string, args: CreateCardArgs): Promise<Card> =>
    request(`/api/admin/engagements/${engagementId}/cards`, {
      method: "POST",
      body: JSON.stringify(args),
    }),

  updateCard: (id: string, args: UpdateCardArgs): Promise<Card> =>
    request(`/api/admin/cards/${id}`, {
      method: "PATCH",
      body: JSON.stringify(args),
    }),

  deleteCard: (id: string): Promise<void> =>
    request(`/api/admin/cards/${id}`, { method: "DELETE" }),

  importMarkdownCards: (
    engagementId: string,
    markdown: string,
  ): Promise<{ created: Card[] }> =>
    request(`/api/admin/engagements/${engagementId}/cards/import-markdown`, {
      method: "POST",
      body: JSON.stringify({ markdown }),
    }),

  uploadAttachment: (file: File): Promise<{ path: string; mime_type: string }> => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    return request("/api/admin/attachments", { method: "POST", body: fd });
  },

  // Server streams the file under the admin's session cookie. Used in
  // <a href target="_blank"> — the browser sends the cookie automatically
  // for same-site requests.
  uploadDownloadUrl: (uploadId: string): string =>
    `${API_BASE}/api/admin/uploads/${uploadId}/download`,
};

/** Real clients (companies), operator-only. Org-scoped on the backend via
 * the same `pulse_member` + `pulse.org_id` RLS as every `/api/admin/*`
 * surface, so a client listed here is invisible to other tenants. Clients
 * are created as a side effect of `adminApi.createEngagement` (with a
 * `client_name`), so there's no create/update/delete here. */
export const clientsApi = {
  list: (): Promise<ClientSummary[]> => request("/api/admin/clients"),
};

// ── Org switching, details, members, invites (operator surface) ───────────

/** Org-scoped admin surface — every endpoint resolves the active org
 * from the session cookie (or API key). Mirrors the backend split in
 * `api/pulse_api/routes/orgs.py`. */
export const orgsApi = {
  listMine: (): Promise<OrgSummary[]> => request("/api/me/orgs"),

  switchOrg: (orgId: string): Promise<OrgSummary> =>
    request("/api/me/switch-org", {
      method: "POST",
      body: JSON.stringify({ org_id: orgId }),
    }),

  me: (): Promise<OrgDetails> => request("/api/orgs/me"),

  updateMe: (args: { name?: string }): Promise<OrgDetails> =>
    request("/api/orgs/me", {
      method: "PATCH",
      body: JSON.stringify(args),
    }),

  /** Replace the org's branding/theme. An empty object resets every field
   * to the product default (the backend treats absent keys as cleared).
   * Returns the refreshed `OrgDetails` with the canonical `branding`. */
  updateBranding: (branding: BrandingSettings): Promise<OrgDetails> =>
    request("/api/orgs/me/branding", {
      method: "PATCH",
      body: JSON.stringify(branding),
    }),

  uploadLogo: (file: File): Promise<{ logo_path: string }> => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    return request("/api/orgs/me/logo", { method: "POST", body: fd });
  },

  deleteLogo: (): Promise<void> =>
    request("/api/orgs/me/logo", { method: "DELETE" }),

  listMembers: (): Promise<MemberRow[]> => request("/api/orgs/me/members"),

  updateMemberRole: (userId: string, role: string): Promise<MemberRow> =>
    request(`/api/orgs/me/members/${userId}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),

  removeMember: (userId: string): Promise<void> =>
    request(`/api/orgs/me/members/${userId}`, { method: "DELETE" }),

  listInvites: (): Promise<InviteSummary[]> =>
    request("/api/orgs/me/invites"),

  createInvite: (args: {
    email: string;
    role: string;
  }): Promise<InviteSummary> =>
    request("/api/orgs/me/invites", {
      method: "POST",
      body: JSON.stringify(args),
    }),

  revokeInvite: (inviteId: string): Promise<void> =>
    request(`/api/orgs/me/invites/${inviteId}`, { method: "DELETE" }),

  /** Paginated activity feed for the active org. Pass `cursor` (from a
   * previous page's `next_cursor`) to load more. Filters by actor user
   * id and/or exact action enum string. */
  listActivity: (args: ListActivityArgs = {}): Promise<ActivityPage> => {
    const params = new URLSearchParams();
    if (args.limit) params.set("limit", String(args.limit));
    if (args.cursor) params.set("cursor", args.cursor);
    if (args.actor_user_id) params.set("actor_user_id", args.actor_user_id);
    if (args.action) params.set("action", args.action);
    const qs = params.toString();
    return request(`/api/orgs/me/activity${qs ? `?${qs}` : ""}`);
  },
};

/** Public invite-acceptance flow. The token IS the auth — no cookie sent. */
export const invitesApi = {
  resolve: (token: string): Promise<InviteMetadata> =>
    request(`/api/invites/${encodeURIComponent(token)}`),

  acceptWithPassword: (
    token: string,
    args: { password: string; name?: string | null },
  ): Promise<PasswordAcceptResponse> =>
    request(`/api/invites/${encodeURIComponent(token)}/accept`, {
      method: "POST",
      body: JSON.stringify({ auth: "password", ...args }),
    }),

  acceptWithOAuth: (
    token: string,
    provider: "google" | "microsoft",
  ): Promise<OAuthAcceptResponse> =>
    request(`/api/invites/${encodeURIComponent(token)}/accept`, {
      method: "POST",
      body: JSON.stringify({ auth: provider }),
    }),
};

// ── Superadmin surface (cross-tenant — PR 5) ──────────────────────────────

/** Row in the superadmin org listing. The `owner_emails` field is a
 * denormalized top-3 owners by joined date — the table can render
 * "managed by Jane, Bob" without a per-row members fetch. */
export interface SuperadminOrgRow {
  id: string;
  name: string;
  slug: string;
  member_count: number;
  pending_invite_count: number;
  created_at: string;
  owner_emails: string[];
}

export interface SuperadminInviteSummary {
  id: string;
  email: string;
  expires_at: string;
}

export interface SuperadminOrgPayload {
  id: string;
  name: string;
  slug: string;
  created_at: string;
}

export interface CreateOrgResult {
  org: SuperadminOrgPayload;
  invite: SuperadminInviteSummary;
}

export interface SuperadminMemberRow {
  user_id: string;
  email: string;
  name: string | null;
  role: string;
  joined_at: string;
}

/** Cross-tenant org management — gated by `users.is_superadmin` server-side.
 * The UI also hides the entry point unless `me.is_superadmin`, but the
 * backend is the source of truth. */
export const superadminApi = {
  listOrgs: (opts: { limit?: number } = {}): Promise<SuperadminOrgRow[]> => {
    const q = opts.limit ? `?limit=${encodeURIComponent(opts.limit)}` : "";
    return request(`/api/superadmin/orgs${q}`);
  },

  createOrg: (args: {
    name: string;
    slug: string;
    owner_email: string;
  }): Promise<CreateOrgResult> =>
    request("/api/superadmin/orgs", {
      method: "POST",
      body: JSON.stringify(args),
    }),

  deleteOrg: (orgId: string): Promise<void> =>
    request(`/api/superadmin/orgs/${encodeURIComponent(orgId)}`, {
      method: "DELETE",
    }),

  listOrgMembers: (orgId: string): Promise<SuperadminMemberRow[]> =>
    request(`/api/superadmin/orgs/${encodeURIComponent(orgId)}/members`),
};

/** Build an absolute URL for an org logo served by
 * `GET /api/orgs/me/logo/{filename}`. The endpoint authenticates with
 * the session cookie (`credentials: include` on fetches works for
 * cross-origin only when the API base is same-origin, which is the
 * production setup behind nginx). Returns `null` if `logo_path` is empty
 * or doesn't match the expected `org-logos/{org_id}/{filename}` shape —
 * an unrecognized path keeps the call out of the network entirely. */
export function orgLogoUrl(logoPath: string | null | undefined): string | null {
  if (!logoPath) return null;
  // Stored as `org-logos/{org_id}/{filename}`; the backend route ignores
  // the org_id prefix in the URL — it's resolved from the auth context.
  const parts = logoPath.split("/");
  if (parts.length < 3 || parts[0] !== "org-logos") return null;
  const filename = parts[parts.length - 1];
  if (!filename) return null;
  return `${API_BASE}/api/orgs/me/logo/${encodeURIComponent(filename)}`;
}
