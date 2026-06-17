// Per-organization branding/theme — single source of truth.
//
// Imported by `app.ts` (client deck), `admin.ts` (admin shell), and
// `settings.ts` (the Brand & theme form). Owns: the canonical font
// catalogue, the `:root` default values, the small colour-math helpers
// used to derive shades, lazy Google-Fonts injection, and the one
// function that pushes a `BrandingSettings` object onto the live
// `:root` custom properties.
//
// The shape of `BrandingSettings` is the cross-team contract — the
// backend's `ALLOWED_FONTS` must equal the `slug`s in `FONT_OPTIONS`
// below, and the default values must equal `:root` in
// `src/styles/pulse.css`.
import type { BrandingSettings } from "./api";

export interface FontOption {
  /** Stable slug stored in `BrandingSettings.font`. Must match the
   * backend `ALLOWED_FONTS` set byte-for-byte. */
  slug: string;
  /** Human-readable label for the settings `<select>`. */
  label: string;
  /** The full `font-family` stack pushed onto `--font-sans`. */
  cssFamily: string;
  /** The `family=` query param for the Google Fonts CSS2 endpoint, or
   * `null` when the font needs no web font (already in `<head>`, or a
   * system stack). */
  googleParam: string | null;
}

// The default `--font-sans` stack from `src/styles/pulse.css`. Kept as a
// named constant so the Plus Jakarta Sans option and BRANDING_DEFAULTS
// reference the exact same string.
const PLUS_JAKARTA_STACK =
  '"Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

/** Ordered font catalogue. The first entry is the product default;
 * `applyBranding` resolves an absent/unknown slug to `plus-jakarta-sans`. */
export const FONT_OPTIONS: readonly FontOption[] = [
  {
    slug: "plus-jakarta-sans",
    label: "Plus Jakarta Sans",
    cssFamily: PLUS_JAKARTA_STACK,
    // Already linked in both index.astro and admin.astro <head>.
    googleParam: null,
  },
  {
    slug: "inter",
    label: "Inter",
    cssFamily:
      '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    googleParam: "Inter:wght@400;500;600;700;800",
  },
  {
    slug: "roboto",
    label: "Roboto",
    cssFamily:
      '"Roboto", -apple-system, system-ui, Helvetica, Arial, sans-serif',
    googleParam: "Roboto:wght@400;500;700",
  },
  {
    slug: "lora",
    label: "Lora (serif)",
    cssFamily: '"Lora", Georgia, "Times New Roman", serif',
    googleParam: "Lora:wght@400;500;600;700",
  },
  {
    slug: "source-serif",
    label: "Source Serif",
    cssFamily: '"Source Serif 4", Georgia, serif',
    googleParam: "Source+Serif+4:wght@400;500;600;700",
  },
  {
    slug: "system-ui",
    label: "System UI",
    cssFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    // No web font — pure system stack.
    googleParam: null,
  },
];

/** Exact `:root` values from `src/styles/pulse.css`. `applyBranding`
 * falls back to these for any unset field so switching orgs (or clearing
 * branding) resets the theme cleanly. */
export const BRANDING_DEFAULTS = {
  brand_color: "#2960F6",
  brand_dark: "#020F82",
  brand_soft: "#E8EEFE",
  background_color: "#F4F4F6",
  text_color: "#0A0F2E",
  font: "plus-jakarta-sans",
} as const;

// ── Colour math ────────────────────────────────────────────────────────────

/** Parse `#RRGGBB` (case-insensitive, leading `#` optional) into an
 * `[r, g, b]` triple, or `null` when the input isn't a valid 6-digit hex. */
function parseHex(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

function toHex(r: number, g: number, b: number): string {
  const clamp = (v: number): number => Math.max(0, Math.min(255, Math.round(v)));
  const h = (v: number): string => clamp(v).toString(16).padStart(2, "0");
  return `#${h(r)}${h(g)}${h(b)}`.toUpperCase();
}

/** Darken a hex colour toward black by factor `f` (0 = unchanged, 1 =
 * black). Returns the input unchanged when it isn't valid hex. */
export function darken(hex: string, f: number): string {
  const rgb = parseHex(hex);
  if (!rgb) return hex;
  const k = 1 - Math.max(0, Math.min(1, f));
  return toHex(rgb[0] * k, rgb[1] * k, rgb[2] * k);
}

/** Mix a hex colour toward white by factor `f` (0 = unchanged, 1 =
 * white). Returns the input unchanged when it isn't valid hex. */
export function mixWithWhite(hex: string, f: number): string {
  const rgb = parseHex(hex);
  if (!rgb) return hex;
  const t = Math.max(0, Math.min(1, f));
  const mix = (c: number): number => c + (255 - c) * t;
  return toHex(mix(rgb[0]), mix(rgb[1]), mix(rgb[2]));
}

// ── Google Fonts lazy loading ────────────────────────────────────────────────

// Slugs whose stylesheet <link> we've already appended this session.
// Idempotent: `ensureFontLoaded` is a no-op once a slug is in here.
const injectedFontSlugs = new Set<string>();

/** Append the Google Fonts stylesheet for `slug` to `<head>` if it has a
 * web font and hasn't been injected yet. No-op for fonts with a `null`
 * `googleParam` (Plus Jakarta Sans — already in `<head>` — and the
 * system stacks). */
export function ensureFontLoaded(slug: string): void {
  if (injectedFontSlugs.has(slug)) return;
  const opt = FONT_OPTIONS.find((o) => o.slug === slug);
  if (!opt || opt.googleParam === null) {
    // Mark resolved so we don't re-scan the catalogue on every apply.
    injectedFontSlugs.add(slug);
    return;
  }
  if (typeof document === "undefined") return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = `https://fonts.googleapis.com/css2?family=${opt.googleParam}&display=swap`;
  document.head.appendChild(link);
  injectedFontSlugs.add(slug);
}

// ── Apply branding to the live document ──────────────────────────────────────

/** Resolve a font slug to its catalogue entry, defaulting to Plus Jakarta
 * Sans when the slug is absent or unrecognized. */
function resolveFont(slug: string | null | undefined): FontOption {
  const found = slug ? FONT_OPTIONS.find((o) => o.slug === slug) : undefined;
  return found ?? FONT_OPTIONS[0];
}

/** Push a `BrandingSettings` object onto the document's `:root` custom
 * properties. Always sets every managed variable — passing `null`/`{}`
 * resets the theme to {@link BRANDING_DEFAULTS}, which is what makes
 * org-switching and "Reset to defaults" clean. The brand colour drives
 * three derived tokens (`--primary`, `--primary-dark`, `--primary-soft`);
 * background and text map straight through; the font is resolved to a
 * catalogue stack and its web font is lazily loaded. */
export function applyBranding(
  branding: BrandingSettings | null | undefined,
): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  const b = branding ?? {};

  // Brand colour → primary + two derived shades.
  if (b.brand_color) {
    root.style.setProperty("--primary", b.brand_color);
    root.style.setProperty("--primary-dark", darken(b.brand_color, 0.25));
    root.style.setProperty("--primary-soft", mixWithWhite(b.brand_color, 0.9));
  } else {
    root.style.setProperty("--primary", BRANDING_DEFAULTS.brand_color);
    root.style.setProperty("--primary-dark", BRANDING_DEFAULTS.brand_dark);
    root.style.setProperty("--primary-soft", BRANDING_DEFAULTS.brand_soft);
  }

  // Background + text map straight through (with defaults).
  root.style.setProperty(
    "--surface-muted",
    b.background_color || BRANDING_DEFAULTS.background_color,
  );
  root.style.setProperty("--ink", b.text_color || BRANDING_DEFAULTS.text_color);

  // Font: resolve slug → stack, lazy-load the web font, set the var.
  const font = resolveFont(b.font);
  ensureFontLoaded(font.slug);
  root.style.setProperty("--font-sans", font.cssFamily);
}
