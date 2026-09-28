import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  LayoutList,
  RotateCw,
  TriangleAlert,
} from "lucide-react";

import { PulseWordmark } from "@/components/admin/Brand";
import { cn } from "@/lib/utils";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { VOICE_PLACEHOLDER } from "./constants";

/** Sticky top: the operator org's logo — or, without one, the org's name in
 * its brand colour (`text-primary` follows the org's branding) — an
 * "n / total" count, and a thin progress line that fills as the recipient
 * advances. The Pulse wordmark is only a last resort when no org name. */
export function TopBar({
  position,
  total,
  orgLogoSrc,
  orgName,
  wide,
}: {
  position: number;
  total: number;
  orgLogoSrc?: string | null;
  orgName?: string | null;
  /** Desktop layout: span the full content width, not the phone column. */
  wide?: boolean;
}): React.ReactElement {
  const pct = total > 0 ? Math.min(100, Math.round((position / total) * 100)) : 0;
  return (
    <header className="sticky top-0 z-10 border-b border-border bg-background/90 backdrop-blur">
      <div
        className={cn(
          "mx-auto flex h-14 w-full items-center justify-between gap-3 px-5",
          wide ? "max-w-7xl" : "max-w-xl",
        )}
      >
        {orgLogoSrc ? (
          <img
            src={orgLogoSrc}
            alt={orgName ?? ""}
            className="h-7 w-auto max-w-[140px] object-contain"
          />
        ) : orgName ? (
          <span className="min-w-0 truncate text-lg font-semibold tracking-tight text-primary">
            {orgName}
          </span>
        ) : (
          <PulseWordmark className="text-lg" />
        )}
        <span className="text-sm tabular-nums text-muted-foreground">
          <span className="font-medium text-foreground">{position}</span> / {total}
        </span>
      </div>
      <div
        role="progressbar"
        aria-valuenow={position}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-label={`Card ${position} of ${total}`}
        className="absolute inset-x-0 -bottom-px h-0.5 bg-transparent"
      >
        <div
          className="h-full bg-primary transition-[width] duration-300 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
    </header>
  );
}

/** Prev / picker / next — a bottom bar kept within thumb reach. */
export function DeckNav({
  position,
  total,
  onBack,
  onForward,
  onPicker,
  backDisabled,
  forwardDisabled,
}: {
  position: number;
  total: number;
  onBack: () => void;
  onForward: () => void;
  onPicker: () => void;
  backDisabled: boolean;
  forwardDisabled: boolean;
}): React.ReactElement {
  return (
    <nav
      aria-label="Card navigation"
      className="mx-auto flex w-full max-w-xl items-center justify-between gap-2 px-5"
    >
      <Button
        variant="ghost"
        onClick={onBack}
        disabled={backDisabled}
        aria-label="Previous card"
        className="h-10 gap-1 px-2 text-muted-foreground"
      >
        <ChevronLeft />
        <span className="hidden sm:inline">Back</span>
      </Button>
      <Button
        variant="outline"
        size="sm"
        onClick={onPicker}
        className="h-9 gap-1.5 rounded-full px-3.5 tabular-nums"
        data-testid="deck-progress"
      >
        <LayoutList className="size-4 text-muted-foreground" />
        {position} of {total}
        <ChevronDown className="size-4 text-muted-foreground" />
      </Button>
      <Button
        variant="ghost"
        onClick={onForward}
        disabled={forwardDisabled}
        aria-label="Next card"
        className="h-10 gap-1 px-2 text-muted-foreground"
      >
        <span className="hidden sm:inline">Next</span>
        <ChevronRight />
      </Button>
    </nav>
  );
}

export function SaveBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}): React.ReactElement {
  return (
    <div
      role="alert"
      className="mx-auto mt-4 flex w-[calc(100%-2.5rem)] max-w-[calc(36rem-2.5rem)] items-center justify-between gap-3 rounded-lg border border-warning/40 bg-warning-soft px-4 py-2.5 text-sm text-amber-900"
    >
      <span className="flex items-center gap-2">
        <TriangleAlert className="size-4 shrink-0" aria-hidden="true" />
        {message}
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={onRetry}
        className="shrink-0 border-warning/50 bg-transparent"
      >
        <RotateCw />
        Retry
      </Button>
    </div>
  );
}

export function ResumeBanner(): React.ReactElement {
  return (
    <div
      role="status"
      className="mx-auto mt-4 w-[calc(100%-2.5rem)] max-w-[calc(36rem-2.5rem)] rounded-lg border border-border bg-muted/50 px-4 py-2.5 text-center text-sm text-muted-foreground"
    >
      Welcome back. Picking up where you left off.
    </div>
  );
}

export function NoteField({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}): React.ReactElement {
  return (
    <label className="mt-5 block">
      <span className="mb-1.5 block text-sm font-medium text-foreground">
        Notes{" "}
        <span className="font-normal text-muted-foreground">Optional</span>
      </span>
      <Textarea
        rows={2}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder={VOICE_PLACEHOLDER}
        className="min-h-16 text-[0.95rem]"
      />
    </label>
  );
}
