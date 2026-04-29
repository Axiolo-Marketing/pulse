import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// Admin Supabase client. Uses the service role key, which bypasses RLS.
// Only loaded after the password gate succeeds. Vite inlines this constant
// into the /admin chunk only.

const url = import.meta.env.PUBLIC_SUPABASE_URL as string | undefined;
const serviceRole = import.meta.env.PUBLIC_SUPABASE_SERVICE_ROLE_KEY as
  | string
  | undefined;

let cached: SupabaseClient | null = null;

export function getAdminClient(): SupabaseClient {
  if (!url || !serviceRole) {
    throw new Error(
      "Admin Supabase env vars missing. Set PUBLIC_SUPABASE_URL and PUBLIC_SUPABASE_SERVICE_ROLE_KEY."
    );
  }
  if (!cached) {
    cached = createClient(url, serviceRole, {
      auth: { persistSession: false, autoRefreshToken: false },
    });
  }
  return cached;
}
