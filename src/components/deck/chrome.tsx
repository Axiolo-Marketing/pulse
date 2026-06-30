import { ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { VOICE_PLACEHOLDER } from "./constants";

export function TopBar({
  position,
  total,
  orgLogoSrc,
  orgName,
  onBack,
  onForward,
  onPicker,
  backDisabled,
  forwardDisabled,
}: {
  position: number;
  total: number;
  orgLogoSrc?: string | null;
  orgName?: string | null;
  onBack: () => void;
  onForward: () => void;
  onPicker: () => void;
  backDisabled: boolean;
  forwardDisabled: boolean;
}): React.ReactElement {
  return (
    <header className="sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-border bg-card/95 px-3 py-2.5 backdrop-blur">
      <span className="flex items-center gap-2 text-base font-semibold text-foreground">
        Pulse
        {orgLogoSrc ? (
          <>
            <span aria-hidden="true" className="text-muted-foreground">
              ·
            </span>
            <img
              src={orgLogoSrc}
              alt={orgName ?? ""}
              className="h-6 w-auto max-w-[120px] object-contain"
            />
          </>
        ) : null}
      </span>
      <nav aria-label="Card navigation" className="flex items-center gap-0.5">
        <Button
          variant="ghost"
          size="icon"
          onClick={onBack}
          disabled={backDisabled}
          aria-label="Previous card"
        >
          <ChevronLeft />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onPicker}
          className="gap-1 font-semibold tabular-nums"
          data-testid="deck-progress"
        >
          {position} of {total}
          <ChevronDown className="size-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={onForward}
          disabled={forwardDisabled}
          aria-label="Next card"
        >
          <ChevronRight />
        </Button>
      </nav>
    </header>
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
      className="mx-auto flex w-full max-w-xl items-center justify-between gap-3 border-b border-warning/40 bg-warning-soft px-4 py-2.5 text-sm text-foreground"
    >
      <span>{message}</span>
      <button
        type="button"
        onClick={onRetry}
        className="shrink-0 rounded-md border border-warning px-3 py-1 text-sm font-semibold text-warning transition-colors hover:bg-warning hover:text-white"
      >
        Retry
      </button>
    </div>
  );
}

export function ResumeBanner(): React.ReactElement {
  return (
    <div
      role="status"
      className="mx-auto w-full max-w-xl border-b border-primary/20 bg-secondary px-4 py-2.5 text-center text-sm font-medium text-secondary-foreground"
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
    <label className="mt-1 block">
      <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Notes (optional)
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
