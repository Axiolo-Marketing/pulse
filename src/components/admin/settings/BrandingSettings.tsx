import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  orgsApi,
  ApiError,
  type OrgDetails,
  type BrandingSettings as Branding,
} from "@/lib/api";
import { applyBranding, FONT_OPTIONS, BRANDING_DEFAULTS } from "@/lib/branding";
import { ConfirmDialog } from "@/components/admin/detail/EngagementDialogs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";

// ── Local colour helpers ────────────────────────────────────────────────────

const HEX_RE = /^#[0-9a-f]{6}$/i;

/** True when `s` is a full `#RRGGBB` hex string. */
function isValidHex(s: string): boolean {
  return HEX_RE.test(s.trim());
}

/** A valid hex, or `null` (→ "use the product default"). */
function cleanHex(s: string): string | null {
  return isValidHex(s) ? s.trim() : null;
}

function parseHex(hex: string): [number, number, number] | null {
  const m = HEX_RE.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(hex.trim().slice(1), 16);
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

/** WCAG relative luminance of a hex colour, or `null` when unparseable. */
function luminance(hex: string): number | null {
  const rgb = parseHex(hex);
  if (!rgb) return null;
  const chan = (v: number): number => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * chan(rgb[0]) + 0.7152 * chan(rgb[1]) + 0.0722 * chan(rgb[2]);
}

/** WCAG contrast ratio between two hex colours, or `null` when unparseable. */
function contrastRatio(fg: string, bg: string): number | null {
  const l1 = luminance(fg);
  const l2 = luminance(bg);
  if (l1 === null || l2 === null) return null;
  const [hi, lo] = l1 >= l2 ? [l1, l2] : [l2, l1];
  return (hi + 0.05) / (lo + 0.05);
}

// ── Colour row (native colour picker + hex text input, bidirectional) ────────

function ColorRow({
  id,
  label,
  value,
  fallback,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  fallback: string;
  onChange: (v: string) => void;
}): React.ReactElement {
  const valid = isValidHex(value);
  const swatch = (valid ? value : fallback).toLowerCase();
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={`${label} colour picker`}
          value={swatch}
          onChange={(e) => onChange(e.target.value)}
          className="h-10 w-12 shrink-0 cursor-pointer rounded-md border border-input bg-card p-1"
        />
        <Input
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="#RRGGBB"
          spellCheck={false}
          autoComplete="off"
          aria-invalid={!valid}
          className="font-mono"
        />
      </div>
      {!valid ? (
        <p className="text-xs text-muted-foreground">
          Using the default — enter a valid #RRGGBB hex.
        </p>
      ) : null}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────

export function BrandingSettings({
  org,
}: {
  org: OrgDetails;
}): React.ReactElement {
  const qc = useQueryClient();

  // Seed each field from saved branding, falling back to the product default
  // so the effective colours always render.
  const [brandColor, setBrandColor] = useState(
    org.branding?.brand_color ?? BRANDING_DEFAULTS.brand_color,
  );
  const [background, setBackground] = useState(
    org.branding?.background_color ?? BRANDING_DEFAULTS.background_color,
  );
  const [text, setText] = useState(
    org.branding?.text_color ?? BRANDING_DEFAULTS.text_color,
  );
  const [font, setFont] = useState(
    org.branding?.font ?? BRANDING_DEFAULTS.font,
  );
  const [error, setError] = useState<string | null>(null);
  const [resetOpen, setResetOpen] = useState(false);

  // Invalid hex → null (→ default) both in preview and on save.
  const buildBranding = (): Branding => ({
    brand_color: cleanHex(brandColor),
    background_color: cleanHex(background),
    text_color: cleanHex(text),
    font,
  });

  // Live preview: push current values onto :root on any change.
  useEffect(() => {
    applyBranding({
      brand_color: cleanHex(brandColor),
      background_color: cleanHex(background),
      text_color: cleanHex(text),
      font,
    });
  }, [brandColor, background, text, font]);

  // Cleanup: on unmount restore the SAVED branding so leaving without saving
  // doesn't strand the live preview. Track the saved value in a ref so the
  // cleanup only fires on unmount — keeping `org.branding` out of the dep
  // array avoids reverting the theme every time the org query refetches
  // (e.g. right after a save/reset).
  const savedBrandingRef = useRef(org.branding);
  useEffect(() => {
    savedBrandingRef.current = org.branding;
  }, [org.branding]);
  useEffect(() => () => applyBranding(savedBrandingRef.current), []);

  const saveMut = useMutation({
    mutationFn: () => orgsApi.updateBranding(buildBranding()),
    onSuccess: (updated) => {
      applyBranding(updated.branding);
      void qc.invalidateQueries({ queryKey: ["orgs", "me"] });
      toast.success("Branding saved.");
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.detail : "Could not save branding."),
  });

  const resetMut = useMutation({
    mutationFn: () => orgsApi.updateBranding({}),
    onSuccess: (updated) => {
      setBrandColor(BRANDING_DEFAULTS.brand_color);
      setBackground(BRANDING_DEFAULTS.background_color);
      setText(BRANDING_DEFAULTS.text_color);
      setFont(BRANDING_DEFAULTS.font);
      applyBranding(updated.branding);
      void qc.invalidateQueries({ queryKey: ["orgs", "me"] });
      toast.success("Branding reset.");
      setResetOpen(false);
    },
    onError: (err) =>
      setError(
        err instanceof ApiError ? err.detail : "Could not reset branding.",
      ),
  });

  // Effective (default-filled) values drive the preview + contrast note.
  const effBrand = cleanHex(brandColor) ?? BRANDING_DEFAULTS.brand_color;
  const effBg = cleanHex(background) ?? BRANDING_DEFAULTS.background_color;
  const effText = cleanHex(text) ?? BRANDING_DEFAULTS.text_color;
  const effFont = (
    FONT_OPTIONS.find((o) => o.slug === font) ?? FONT_OPTIONS[0]
  ).cssFamily;
  const ratio = contrastRatio(effText, effBg);

  const pending = saveMut.isPending || resetMut.isPending;

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold text-foreground">Brand &amp; theme</h2>
        <p className="text-sm text-muted-foreground">
          Colours and font applied across your engagement decks and the admin
          console.
        </p>
      </div>

      <Separator />

      <div className="grid gap-6 md:grid-cols-2">
        {/* Controls */}
        <div className="flex flex-col gap-4">
          <ColorRow
            id="branding-brand-color"
            label="Brand colour"
            value={brandColor}
            fallback={BRANDING_DEFAULTS.brand_color}
            onChange={(v) => setBrandColor(v)}
          />
          <ColorRow
            id="branding-background-color"
            label="Background"
            value={background}
            fallback={BRANDING_DEFAULTS.background_color}
            onChange={(v) => setBackground(v)}
          />
          <ColorRow
            id="branding-text-color"
            label="Text"
            value={text}
            fallback={BRANDING_DEFAULTS.text_color}
            onChange={(v) => setText(v)}
          />
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="branding-font">Font</Label>
            <Select value={font} onValueChange={setFont}>
              <SelectTrigger id="branding-font" className="w-full">
                <SelectValue placeholder="Select a font" />
              </SelectTrigger>
              <SelectContent>
                {FONT_OPTIONS.map((o) => (
                  <SelectItem key={o.slug} value={o.slug}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Live preview */}
        <div className="flex flex-col gap-2">
          <Label>Preview</Label>
          <div
            className="rounded-lg border border-border p-4"
            style={{
              backgroundColor: effBg,
              color: effText,
              fontFamily: effFont,
            }}
          >
            <p className="text-sm font-semibold">Sample card</p>
            <p className="mt-1 text-sm">
              The quick brown fox jumps over the lazy dog.
            </p>
            <button
              type="button"
              className="mt-3 rounded-md px-3 py-1.5 text-sm font-semibold"
              style={{ backgroundColor: effBrand, color: "#ffffff" }}
            >
              Primary action
            </button>
          </div>
          {ratio !== null && ratio < 4.5 ? (
            <p className="text-xs text-muted-foreground" role="note">
              Text-on-background contrast is {ratio.toFixed(2)}:1 — below the
              WCAG AA minimum of 4.5:1.
            </p>
          ) : null}
        </div>
      </div>

      {error ? (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}

      <div className="flex items-center gap-2">
        <Button
          onClick={() => {
            setError(null);
            saveMut.mutate();
          }}
          disabled={pending}
        >
          Save branding
        </Button>
        <Button
          variant="outline"
          onClick={() => {
            setError(null);
            setResetOpen(true);
          }}
          disabled={pending}
        >
          Reset to defaults
        </Button>
      </div>

      <ConfirmDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        title="Reset branding?"
        description="This restores the default Pulse colours and font for everyone in your organization. This can't be undone."
        confirmLabel="Reset"
        destructive
        pending={resetMut.isPending}
        onConfirm={() => {
          setError(null);
          resetMut.mutate();
        }}
      />
    </section>
  );
}
