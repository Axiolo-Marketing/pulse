import { cn } from "@/lib/utils";

/** A titled card block used across the settings tabs. */
export function SettingsSection({
  title,
  description,
  action,
  children,
  className,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}): React.ReactElement {
  return (
    <section className={cn("rounded-lg border border-border bg-card p-5", className)}>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          {description ? (
            <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      {children}
    </section>
  );
}

/** Inline status line under a form — success (green) or error (red). */
export function FormMessage({
  message,
}: {
  message: { kind: "success" | "error"; text: string } | null;
}): React.ReactElement | null {
  if (!message) return null;
  return (
    <p
      role="status"
      aria-live="polite"
      className={cn(
        "text-sm",
        message.kind === "error" ? "text-destructive" : "text-success",
      )}
    >
      {message.text}
    </p>
  );
}
