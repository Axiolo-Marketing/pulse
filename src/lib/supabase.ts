import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// Browser-side Supabase client. Uses the anon key only.
//
// Pulse's auth model is a long random token in the URL (?t=...). Every
// request includes that token in an x-pulse-token header; RLS policies on
// the database read the header and only return rows belonging to the
// matching client. Without the header, the anon role sees nothing.

const url = (import.meta.env.PUBLIC_SUPABASE_URL ??
  import.meta.env.SUPABASE_URL) as string | undefined;
const anonKey = (import.meta.env.PUBLIC_SUPABASE_ANON_KEY ??
  import.meta.env.SUPABASE_ANON_KEY) as string | undefined;

export function createPulseClient(token: string): SupabaseClient {
  if (!url || !anonKey) {
    throw new Error(
      "Supabase env vars missing. Set PUBLIC_SUPABASE_URL and PUBLIC_SUPABASE_ANON_KEY."
    );
  }
  return createClient(url, anonKey, {
    auth: { persistSession: false, autoRefreshToken: false },
    global: {
      headers: { "x-pulse-token": token },
    },
  });
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

export interface Client {
  id: string;
  name: string;
  org_name: string | null;
  engagement_name: string | null;
  token: string;
  created_at: string;
  last_active_at: string | null;
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
