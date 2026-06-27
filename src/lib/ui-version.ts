// v1/v2 UI toggle (TS side).
//
// v2 is the React/shadcn rewrite; it ships side by side with v1 and is OPT-IN
// until proven (see ~/.claude/plans/yes-i-want-to-kind-pearl.md). v2 pages live
// under `/v2/...`; v1 stays the default.
//
// The actual redirect runs as a tiny inline script in each v1 page <head>
// (see src/components/UiGate.astro) so it fires before paint and can't import a
// module. These helpers are the TS-side equivalents for app code — e.g. a
// future "Try the new UI" / "Switch back" control in settings.

export const UI_COOKIE = "pulse_ui";
const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

/** True when the visitor has opted into v2 (sticky `pulse_ui=v2` cookie set). */
export function prefersV2(): boolean {
  if (typeof document === "undefined") return false;
  return document.cookie
    .split("; ")
    .some((c) => c === `${UI_COOKIE}=v2`);
}

/** Opt into v2: set the sticky cookie. Caller decides whether to navigate. */
export function optInToV2(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${UI_COOKIE}=v2; path=/; max-age=${ONE_YEAR_SECONDS}; samesite=lax`;
}

/** Opt back out to v1: clear the cookie. */
export function optOutToV1(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${UI_COOKIE}=; path=/; max-age=0; samesite=lax`;
}
