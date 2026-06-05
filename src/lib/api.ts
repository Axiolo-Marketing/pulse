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

export interface Client {
  id: string;
  name: string;
  /** Legacy free-form customer-org text on the client row (kept for
   * backwards compat). The post-multi-tenant operator-side org is
   * surfaced separately via `org_logo_path` + the brand wordmark. */
  org_name: string | null;
  engagement_name: string | null;
  token?: string;       // present in admin views, omitted from /api/me
  brief: string | null;
  created_at: string;
  last_active_at: string | null;
  /** Optional brand logo path for the operator's organization. When
   * `/api/me` returns this, the client-facing deck renders it in the
   * top-bar instead of the default Axiolo wordmark. Backend wires this
   * through in a follow-up — frontend already handles the field
   * gracefully when absent (treats it as `null`). */
  org_logo_path?: string | null;
}

export interface Card {
  id: string;
  client_id: string;
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
  client_id: string;
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
  client_id: string;
  file_name: string;
  file_size_bytes: number;
  storage_path: string;
  mime_type: string | null;
  uploaded_at: string;
}

// ── Client-facing API ─────────────────────────────────────────────────────

export const clientApi = {
  me: (token: string): Promise<Client> => request("/api/me", { token }),

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

  upload: (token: string, cardId: string, file: File): Promise<UploadRow> => {
    const fd = new FormData();
    fd.append("card_id", cardId);
    fd.append("file", file, file.name);
    return request("/api/uploads", { method: "POST", token, body: fd });
  },

  deleteUpload: (token: string, uploadId: string): Promise<void> =>
    request(`/api/uploads/${uploadId}`, { method: "DELETE", token }),
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
  name: string;
  org_name: string | null;
  engagement_name: string | null;
  token: string;
  brief: string | null;
  created_at: string;
  last_active_at: string | null;
  answered_count: number;
  skipped_count: number;
  total_cards: number;
}

export interface EngagementDetail {
  client: Client & { token: string };
  cards: Card[];
  responses: ClientResponse[];
  uploads: UploadRow[];
}

export interface CreateClientArgs {
  name: string;
  org_name?: string | null;
  engagement_name?: string | null;
}

export interface UpdateClientArgs {
  name?: string;
  org_name?: string | null;
  engagement_name?: string | null;
  brief?: string | null;
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
  listClients: (): Promise<EngagementSummary[]> =>
    request("/api/admin/clients"),

  getClient: (id: string): Promise<EngagementDetail> =>
    request(`/api/admin/clients/${id}`),

  createClient: (args: CreateClientArgs): Promise<EngagementSummary> =>
    request("/api/admin/clients", {
      method: "POST",
      body: JSON.stringify(args),
    }),

  updateClient: (id: string, args: UpdateClientArgs): Promise<EngagementSummary> =>
    request(`/api/admin/clients/${id}`, {
      method: "PATCH",
      body: JSON.stringify(args),
    }),

  rotateToken: (id: string): Promise<EngagementSummary> =>
    request(`/api/admin/clients/${id}/rotate-token`, { method: "POST" }),

  deleteEngagement: (id: string): Promise<void> =>
    request(`/api/admin/clients/${id}`, { method: "DELETE" }),

  createCard: (clientId: string, args: CreateCardArgs): Promise<Card> =>
    request(`/api/admin/clients/${clientId}/cards`, {
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
    clientId: string,
    markdown: string,
  ): Promise<{ created: Card[] }> =>
    request(`/api/admin/clients/${clientId}/cards/import-markdown`, {
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
