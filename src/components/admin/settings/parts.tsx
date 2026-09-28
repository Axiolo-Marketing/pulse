import { cn } from "@/lib/utils";

/** A titled settings card. Header (title, description, optional header
 * action), a body, and an optional footer bar that holds the card's primary
 * button with a hint on the left — the pattern shared across the operator
 * console. Pass `onSubmit` to make the whole card a <form> (so the footer's
 * submit button and Enter-in-a-field both submit). `flush` drops the body
 * padding for edge-to-edge lists and tables. */
export function SettingsSection({
  title,
  description,
  action,
  children,
  footer,
  footerHint,
  onSubmit,
  flush,
  className,
}: {
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  children?: React.ReactNode;
  footer?: React.ReactNode;
  footerHint?: React.ReactNode;
  onSubmit?: () => void;
  flush?: boolean;
  className?: string;
}): React.ReactElement {
  const inner = (
    <>
      <div className="flex flex-col gap-3 px-5 pt-5 pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-foreground">{title}</h2>
          {description ? (
            <p className="mt-1 text-sm text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      {children ? (
        <div className={cn(flush ? "border-t border-border" : "px-5 pb-5")}>
          {children}
        </div>
      ) : null}
      {footer || footerHint ? (
        <div className="flex min-h-14 flex-wrap items-center justify-between gap-3 border-t border-border bg-muted/40 px-5 py-3">
          <div className="text-sm text-muted-foreground">{footerHint}</div>
          <div className="flex items-center gap-2">{footer}</div>
        </div>
      ) : null}
    </>
  );

  const cls = cn("overflow-hidden rounded-lg border border-border bg-card", className);
  if (onSubmit) {
    return (
      <form
        className={cls}
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
      >
        {inner}
      </form>
    );
  }
  return <section className={cls}>{inner}</section>;
}

/** Inline status line — success (green) or error (red). Fits in a
 * SettingsSection `footerHint`. */
export function FormMessage({
  message,
}: {
  message: { kind: "success" | "error"; text: string } | null;
}): React.ReactElement | null {
  if (!message) return null;
  return (
    <span
      role="status"
      aria-live="polite"
      className={cn(
        "text-sm",
        message.kind === "error" ? "text-destructive" : "text-success",
      )}
    >
      {message.text}
    </span>
  );
}

/** Initial-letter avatar for a person row. */
export function PersonAvatar({ label }: { label: string }): React.ReactElement {
  return (
    <span
      aria-hidden="true"
      className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-foreground"
    >
      {(label[0] ?? "?").toUpperCase()}
    </span>
  );
}

/** Small outline role/status pill. */
export function Pill({
  children,
  tone = "default",
}: {
  children: React.ReactNode;
  tone?: "default" | "strong";
}): React.ReactElement {
  return (
    <span
      className={cn(
        "whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-none",
        tone === "strong"
          ? "border-foreground/15 bg-foreground/5 text-foreground"
          : "border-border text-muted-foreground",
      )}
    >
      {children}
    </span>
  );
}
