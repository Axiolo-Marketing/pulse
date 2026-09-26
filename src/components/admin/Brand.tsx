import { cn } from "@/lib/utils";

/** The "pulse" product wordmark — Epilogue bold italic in the Pulse accent,
 * the same treatment as the quickhost.ing wordmark. Epilogue is loaded by the
 * v2 admin page shell. */
export function PulseWordmark({
  className,
}: {
  className?: string;
}): React.ReactElement {
  return (
    <span
      className={cn(
        "font-display text-xl font-bold italic leading-none tracking-tight text-brand",
        className,
      )}
    >
      pulse
    </span>
  );
}

/** Site footer: copyright, "Made by Axiolo" credit, legal links. Axiolo is
 * the maker, not the product, so it lives here rather than in the header. */
export function AdminFooter(): React.ReactElement {
  return (
    <footer className="mt-auto border-t border-border">
      <div className="mx-auto flex w-full max-w-6xl flex-col items-center gap-3 px-4 py-5 text-xs text-muted-foreground sm:flex-row sm:justify-between">
        <span>
          © {new Date().getFullYear()} Axiolo LLC. All rights reserved.
        </span>
        <a
          href="https://axiolo.com"
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1.5 hover:text-foreground"
        >
          Made by
          <img src="/axiolo-logo.svg" alt="Axiolo" width="58" height="16" />
        </a>
        <nav className="flex gap-4">
          <a href="/terms" className="hover:text-foreground">
            Terms of Use
          </a>
          <a href="/privacy" className="hover:text-foreground">
            Privacy
          </a>
        </nav>
      </div>
    </footer>
  );
}
