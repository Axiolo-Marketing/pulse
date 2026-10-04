/**
 * Safe `?return_to=` handling. The MCP OAuth consent flow sends signed-out
 * operators to `/admin/?return_to=/authorize/consent?...`; after sign-in we
 * bounce them back. Only same-origin relative paths are honored so a crafted
 * link can never redirect the browser off Pulse.
 */

const OAUTH_KEY = "pulse:return_to";
const OAUTH_TTL_MS = 10 * 60 * 1000;

/** Validate a raw value; returns `path?query#hash` or null. */
export function sanitizeReturnTo(
  raw: string | null | undefined,
  origin: string = window.location.origin,
): string | null {
  if (!raw) return null;
  // Must be a single-slash relative path. Rejects `//host`, `/\host`,
  // absolute URLs, `javascript:`, and anything with control chars/backslashes.
  if (!raw.startsWith("/") || raw.startsWith("//")) return null;
  // eslint-disable-next-line no-control-regex
  if (/[\\\u0000-\u001f\u007f]/.test(raw)) return null;
  let resolved: URL;
  try {
    resolved = new URL(raw, origin);
  } catch {
    return null;
  }
  if (resolved.origin !== origin) return null;
  return resolved.pathname + resolved.search + resolved.hash;
}

/** `return_to` from the current URL's query string, sanitized. */
export function readReturnTo(search: string = window.location.search): string | null {
  return sanitizeReturnTo(new URLSearchParams(search).get("return_to"));
}

/** Remember a return target across the OAuth round-trip (same tab). */
export function stashReturnTo(target: string | null): void {
  try {
    if (!target) {
      sessionStorage.removeItem(OAUTH_KEY);
      return;
    }
    sessionStorage.setItem(OAUTH_KEY, JSON.stringify({ target, at: Date.now() }));
  } catch {
    /* storage unavailable — fall back to no return_to */
  }
}

/** Pop a stashed target (validated, unexpired), clearing it. */
export function takeStashedReturnTo(): string | null {
  try {
    const raw = sessionStorage.getItem(OAUTH_KEY);
    if (!raw) return null;
    sessionStorage.removeItem(OAUTH_KEY);
    const { target, at } = JSON.parse(raw) as { target?: string; at?: number };
    if (typeof at !== "number" || Date.now() - at > OAUTH_TTL_MS) return null;
    return sanitizeReturnTo(target);
  } catch {
    return null;
  }
}

/** Navigate to the requested return target if present; true when navigating.
 * `replace`, not `assign`: leaving `/admin/?return_to=…` in history would
 * bounce the Back button straight back to the target. */
export function honorReturnTo(): boolean {
  const target = readReturnTo() ?? takeStashedReturnTo();
  if (!target) return false;
  window.location.replace(target);
  return true;
}
